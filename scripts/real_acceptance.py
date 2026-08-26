import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_URL = os.environ.get("AURA_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
TOKEN = os.environ.get("AURA_TOKEN") or os.environ.get("AURA_SERVER_AUTH_TOKEN") or dotenv_values(ENV_PATH).get("AURA_SERVER_AUTH_TOKEN") or ""

def http_post(endpoint: str, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))

def invoke_device_tool(tool: str, arguments: dict = None) -> dict:
    return http_post("/api/device/invoke", {
        "tool": tool,
        "arguments": arguments or {},
        "timeout_s": 60
    })

def capture_fresh_device_observation():
    app_rep = invoke_device_tool("android.get_foreground_app")
    tree_rep = invoke_device_tool("android.get_ui_tree")
    obs_list = []
    if app_rep.get("ok") and app_rep.get("observation"):
        obs_list.append(app_rep["observation"])
    if tree_rep.get("ok") and tree_rep.get("observation"):
        obs_list.append(tree_rep["observation"])
    return obs_list

def run_agent_goal(goal: str, session_id: str = "real_device_acceptance"):
    print(f"\n==================================================")
    print(f"STARTING AGENT GOAL: {goal}")
    print(f"==================================================")
    run_id = ""
    pending_results = []
    pending_observations = []
    max_steps = 15
    trace = []

    for step_num in range(1, max_steps + 1):
        print(f"\n--- STEP {step_num} (Run ID: {run_id or 'NEW'}) ---")
        
        fresh_obs = capture_fresh_device_observation()
        all_obs = fresh_obs + pending_observations
        
        step_payload = {
            "session_id": session_id,
            "goal": goal if not run_id else "",
            "run_id": run_id,
            "observations": all_obs,
            "tool_results": pending_results,
        }
        pending_results = []
        pending_observations = []
        
        step_resp = http_post("/api/agent/step", step_payload)
        run_id = step_resp.get("run_id", run_id)
        status = step_resp.get("status")
        stop_reason = step_resp.get("stop_reason")
        directive = step_resp.get("directive") or {}
        directive_type = directive.get("type")
        
        print(f"Server Response: status={status}, stop_reason={stop_reason}, directive_type={directive_type}")
        
        if directive_type == "final" or not directive_type:
            final_msg = directive.get("message") or step_resp.get("message", "")
            print(f"FINAL RESULT: {final_msg} (Stop reason: {stop_reason})")
            trace.append({
                "step": step_num,
                "type": "final",
                "message": final_msg,
                "status": status,
                "stop_reason": stop_reason,
            })
            return {
                "goal": goal,
                "run_id": run_id,
                "status": status,
                "stop_reason": stop_reason,
                "trace": trace,
                "final_message": final_msg,
            }
            
        if directive_type == "tool_calls":
            tool_calls = directive.get("tool_calls", [])
            print(f"Directive has {len(tool_calls)} tool call(s):")
            for tc in tool_calls:
                call_id = tc.get("tool_call_id")
                tool_name = tc.get("tool")
                args = tc.get("arguments", {})
                print(f"  -> Invoking on real phone: {tool_name} with args: {args}")
                
                exec_report = invoke_device_tool(tool_name, args)
                ok = exec_report.get("ok", False)
                res = exec_report.get("result", {})
                post = exec_report.get("postcondition")
                obs = exec_report.get("observation")
                obs_id = exec_report.get("observation_id", "")
                
                print(f"     Outcome: ok={ok}, postcondition={post}, obs_id={obs_id}")
                
                trace.append({
                    "step": step_num,
                    "tool": tool_name,
                    "arguments": args,
                    "ok": ok,
                    "result": res,
                    "postcondition": post,
                    "observation_id": obs_id,
                })
                
                envelope = {
                    "tool_call_id": call_id,
                    "tool": tool_name,
                    "arguments": args,
                    "ok": ok,
                    "result": res,
                    "error": exec_report.get("error"),
                    "postcondition": post,
                    "observation_id": obs_id,
                }
                pending_results.append(envelope)
                if obs:
                    pending_observations.append(obs)
    
    print(f"Exceeded max steps ({max_steps})")
    return {"goal": goal, "run_id": run_id, "status": "exceeded", "trace": trace}

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    
    # Test 5: Single-step goal "Mở YouTube"
    r5 = run_agent_goal("Mở YouTube", session_id="test_session_open_yt")
    print("\nResult Step 5:", json.dumps(r5, indent=2, ensure_ascii=False))
