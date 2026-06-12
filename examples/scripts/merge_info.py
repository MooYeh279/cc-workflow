"""Merge script for complex workflow — combines project name + features.

Reads JSON from stdin containing upstream outputs from two parallel nodes,
merges them, and outputs a combined result as JSON to stdout.
"""
import json
import sys


def main():
    try:
        raw = sys.stdin.read()
        context = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"merged": False, "brief": f"Invalid stdin JSON: {e}"}))
        sys.exit(1)

    upstream = context.get("upstream", {})

    name_info = upstream.get("gen_project_name", {})
    feat_info = upstream.get("gen_features", {})

    project_name = name_info.get("project_name", "Unknown")
    slogan = name_info.get("slogan", "")
    features = feat_info.get("features", [])
    difficulty = feat_info.get("difficulty", "unknown")

    brief = (
        f"项目: {project_name} | 标语: {slogan} | "
        f"功能({len(features)}项): {', '.join(features)} | "
        f"难度: {difficulty}"
    )

    result = {
        "merged": True,
        "brief": brief,
        "project_name": project_name,
        "slogan": slogan,
        "features": features,
        "difficulty": difficulty,
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
