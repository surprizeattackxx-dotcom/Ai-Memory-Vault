import json, sys, os

def parse(path):
    tools_used = []
    final_text = None
    result_meta = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t == "assistant":
                msg = obj.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tools_used.append({"name": block.get("name"), "input": block.get("input")})
                    elif block.get("type") == "text":
                        final_text = block.get("text")
            elif t == "result":
                result_meta = {
                    "subtype": obj.get("subtype"),
                    "is_error": obj.get("is_error"),
                    "num_turns": obj.get("num_turns"),
                    "result": obj.get("result"),
                }
    return tools_used, final_text, result_meta

if __name__ == "__main__":
    path = sys.argv[1]
    tools, text, meta = parse(path)
    print("=== TOOLS USED ===")
    for tu in tools:
        name = tu["name"]
        inp = tu["input"]
        if name in ("Read", "Grep", "Glob"):
            print(f"  {name}: {inp.get('file_path') or inp.get('pattern') or inp.get('path')}")
        elif name in ("Write", "Edit"):
            print(f"  {name}: {inp.get('file_path')}")
        elif name == "Bash":
            print(f"  Bash: {inp.get('command')}")
        else:
            print(f"  {name}: {str(inp)[:150]}")
    print(f"\n=== RESULT META === {meta}")
    print("\n=== FINAL ASSISTANT TEXT ===")
    print(text or meta.get("result") or "(none captured)")
