"""
Module: AI Agent (Groq / Llama 3.3-70b & Gemini LLM Integration)
===================================================================
Provides AI-assisted natural language intent parsing and automated 
Root Cause Analysis (RCA) log diagnostics with exponential backoff and rate-limit safety.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def _call_groq_api(messages: List[Dict[str, str]], temperature: float = 0.2) -> Optional[str]:
    """
    Invokes Groq API with llama-3.3-70b-versatile model.
    Implements exponential backoff for rate limit (429) protection.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.info("GROQ_API_KEY not configured in environment.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1024,
    }

    data = json.dumps(payload).encode("utf-8")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(GROQ_API_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait_time = (2 ** attempt) * 1.5
                logger.warning("Groq API rate limit hit (429). Retrying in %.1fs...", wait_time)
                time.sleep(wait_time)
            else:
                logger.error("Groq API error HTTP %d: %s", exc.code, exc.read().decode("utf-8", errors="replace"))
                break
        except Exception as exc:
            logger.error("Failed to connect to Groq API: %s", exc)
            break

    return None


def parse_natural_language_intent(prompt: str) -> Dict[str, Any]:
    """
    Parses a user's natural language request into structured provisioning parameters.
    Uses Llama 3.3-70b via Groq API with rule-based fallback.
    """
    sys_prompt = (
        "You are an expert Oracle Exadata DBA Assistant. "
        "Extract database provisioning intent from the user's input into JSON.\n"
        "Return ONLY a JSON object matching this schema:\n"
        "{\n"
        '  "db_name": "string (1-8 chars, alphanumeric, letter+digit mix, no trailing digit)",\n'
        '  "db_unique_name": "string (1-15 chars, alphanumeric with _, letter+digit mix, no trailing digit)",\n'
        '  "target_cluster_id": "string (default: cluster-exa-dev01)",\n'
        '  "source_cluster_id": "string or null (cluster-exa-prod01 if clone)",\n'
        '  "provisioning_type": "seed" or "clone",\n'
        '  "explanation": "brief description"\n'
        "}\n"
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]

    response_text = _call_groq_api(messages, temperature=0.1)

    if response_text:
        try:
            # Extract JSON block if wrapped in markdown
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception as exc:
            logger.warning("Failed to parse LLM JSON response: %s", exc)

    # ── Fallback Rule-Based Parser ──
    prompt_lower = prompt.lower()
    is_clone = "clone" in prompt_lower or "duplicate" in prompt_lower or "copy" in prompt_lower

    # Simple name extraction heuristic
    name_match = re.search(r"\b([a-zA-Z]+[0-9]+[a-zA-Z]+)\b", prompt)
    extracted_name = name_match.group(1).lower() if name_match else ("clndb1a" if is_clone else "mydb1a")

    return {
        "db_name": extracted_name[:8],
        "db_unique_name": f"{extracted_name[:8]}_site1"[:15],
        "target_cluster_id": "cluster-exa-dev01",
        "source_cluster_id": "cluster-exa-prod01" if is_clone else None,
        "provisioning_type": "clone" if is_clone else "seed",
        "explanation": f"Rule-parsed intent: {'Clone' if is_clone else 'Seed'} build for SID={extracted_name[:8].upper()}.",
    }


def diagnose_provisioning_error(log_lines: List[str]) -> Dict[str, Any]:
    """
    Analyzes failed provisioning logs / ORA- errors and generates Root Cause Analysis (RCA).
    """
    chunked_logs = log_lines[-30:]  # Chunk last 30 lines to stay well within token limits
    log_text = "\n".join(chunked_logs)

    sys_prompt = (
        "You are a Senior Oracle DBA & DevOps Engineer. "
        "Analyze the provided execution log snippet from a failed database provisioning run.\n"
        "Return ONLY a JSON object with this schema:\n"
        "{\n"
        '  "root_cause": "concise explanation of why it failed",\n'
        '  "ora_code": "ORA-XXXXX code if present or null",\n'
        '  "recommended_fix": "step-by-step resolution guidance",\n'
        '  "confidence": "high/medium/low"\n'
        "}\n"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Logs:\n{log_text}"},
    ]

    response_text = _call_groq_api(messages, temperature=0.2)

    if response_text:
        try:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass

    # ── Fallback Rule-Based RCA ──
    ora_match = re.search(r"(ORA-\d{5})", log_text)
    ora_code = ora_match.group(1) if ora_match else None

    if "Docker error" in log_text or "daemon" in log_text:
        return {
            "root_cause": "Docker daemon unreachable or container engine stopped.",
            "ora_code": None,
            "recommended_fix": "Ensure Docker Desktop is running and execute 'docker compose up -d' from project root.",
            "confidence": "high",
        }
    elif ora_code:
        return {
            "root_cause": f"Oracle database engine returned error {ora_code}.",
            "ora_code": ora_code,
            "recommended_fix": f"Inspect Oracle alert log inside container and verify init parameters for {ora_code}.",
            "confidence": "medium",
        }
    else:
        return {
            "root_cause": "Provisioning execution interrupted or exited with non-zero status code.",
            "ora_code": None,
            "recommended_fix": "Check container system logs and verify storage path permissions in /u01/oradata/staging.",
            "confidence": "medium",
        }
