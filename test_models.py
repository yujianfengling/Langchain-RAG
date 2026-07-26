"""
ModelProvider Architecture - Real API Test
Tests: mimo, deepseek, qwen via POST /api/chat
"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

def get_subject_id():
    """Get a valid subject_id from knowledge list"""
    try:
        r = requests.get(f"{BASE_URL}/api/knowledge/list", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("subject_id", "default")
            elif isinstance(data, dict) and "data" in data:
                items = data["data"]
                if isinstance(items, list) and len(items) > 0:
                    return items[0].get("subject_id", "default")
        return "default"
    except:
        return "default"


def test_chat(model_name, subject_id, message="你好，请介绍一下自己"):
    """Test a single model via /api/chat"""
    print("=" * 60)
    print("TEST: " + model_name)
    print("=" * 60)
    
    start_time = time.time()
    try:
        r = requests.post(
            f"{BASE_URL}/api/chat",
            json={"question": message, "subject_id": subject_id, "model": model_name},
            timeout=120
        )
        elapsed = time.time() - start_time
        
        print("  HTTP Status: " + str(r.status_code))
        print("  Latency: " + str(round(elapsed, 2)) + "s")
        
        if r.status_code == 200:
            data = r.json()
            print("  Response keys: " + str(list(data.keys())))
            
            if "answer" in data:
                answer = data["answer"]
                print("  Answer (first 300 chars):")
                print("    " + answer[:300])
                print("  Answer length: " + str(len(answer)) + " chars")
                
                if "model" in data:
                    print("  Model in response: " + str(data["model"]))
                if "latency" in data:
                    print("  Server latency: " + str(round(data["latency"], 2)) + "s")
                
                print("  RESULT: PASS")
                return {"status": "PASS", "model": model_name, "latency": elapsed, "answer_len": len(answer)}
            elif "error" in data:
                print("  Error: " + str(data["error"]))
                print("  RESULT: FAIL (API error)")
                return {"status": "FAIL", "model": model_name, "error": data["error"]}
            else:
                print("  Full response: " + json.dumps(data, ensure_ascii=False)[:500])
                print("  RESULT: FAIL (unexpected format)")
                return {"status": "FAIL", "model": model_name, "error": "unexpected format"}
        else:
            print("  Response: " + r.text[:500])
            print("  RESULT: FAIL (HTTP " + str(r.status_code) + ")")
            return {"status": "FAIL", "model": model_name, "error": "HTTP " + str(r.status_code)}
            
    except requests.exceptions.ConnectionError:
        print("  RESULT: FAIL (Connection refused)")
        return {"status": "FAIL", "model": model_name, "error": "Connection refused"}
    except requests.exceptions.Timeout:
        print("  RESULT: FAIL (Timeout after 120s)")
        return {"status": "FAIL", "model": model_name, "error": "Timeout"}
    except Exception as e:
        print("  Exception: " + str(type(e).__name__) + ": " + str(e))
        print("  RESULT: FAIL (Exception)")
        return {"status": "FAIL", "model": model_name, "error": str(e)}


def test_health():
    """Test health endpoint"""
    print("=" * 60)
    print("HEALTH CHECK")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=10)
        print("  Status: " + str(r.status_code))
        data = r.json()
        print("  Response: " + json.dumps(data, ensure_ascii=False)[:500])
        
        # Check LLM service status
        if "data" in data and "services" in data["data"]:
            services = data["data"]["services"]
            print("  LLM service: " + str(services.get("llm", "unknown")))
            print("  RAG service: " + str(services.get("rag", "unknown")))
            print("  Config service: " + str(services.get("config", "unknown")))
        
        return r.status_code == 200
    except Exception as e:
        print("  Exception: " + str(e))
        return False


def test_models_list():
    """Test models list endpoint"""
    print("=" * 60)
    print("MODELS LIST")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/api/models", timeout=10)
        print("  Status: " + str(r.status_code))
        data = r.json()
        print("  Response: " + json.dumps(data, ensure_ascii=False)[:500])
        return r.status_code == 200
    except Exception as e:
        print("  Exception: " + str(e))
        return False


if __name__ == "__main__":
    print("")
    print("=" * 60)
    print("   ModelProvider Architecture - Real API Test")
    print("=" * 60)
    print("")
    
    results = {}
    
    # Health check
    results["health"] = test_health()
    print("")
    
    # Models list
    results["models_list"] = test_models_list()
    print("")
    
    # Get subject_id
    subject_id = get_subject_id()
    print("Using subject_id: " + subject_id)
    print("")
    
    # Test each model
    for model in ["mimo", "deepseek", "qwen"]:
        result = test_chat(model, subject_id)
        results[model] = result["status"] == "PASS"
        print("")
    
    # Summary
    print("")
    print("=" * 60)
    print("   SUMMARY")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        icon = "[OK]" if passed else "[FAIL]"
        print("  " + icon + " " + test_name + ": " + status)
    
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print("")
    print("  Total: " + str(passed_count) + "/" + str(total) + " passed")
    
    sys.exit(0 if all(results.values()) else 1)
