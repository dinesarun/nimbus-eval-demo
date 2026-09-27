"""Command-line eval run — the version you'd put in CI.

    python run_eval.py --prompt v1
    python run_eval.py --prompt v2 --compare v1

Exit code 0 = SHIP, 1 = BLOCK. That's the whole CI integration.
"""
import argparse
import sys

from app import llm
from evals import runner

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def mark(ok):
    return f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"


def print_row(row):
    if "error" in row:
        print(f"  {row['id']:<6} {RED}ERROR{RESET} {row['error']}")
        return
    verdicts = [runner.row_passes(row, m) for m in runner.THRESHOLDS]
    ok = all(v is not False for v in verdicts)
    print(f"  {row['id']:<6} {mark(ok)}  {DIM}{row['question'][:70]}{RESET}")


def print_report(result, regressions=None):
    s = result["summary"]
    print(f"\n{BOLD}Prompt {result['version']}{RESET}  "
          f"{DIM}bot={result['models']['bot']} judge={result['models']['judge']}{RESET}")
    print(f"  {'metric':<14}{'score':>10}{'gate':>9}")
    for name, m in s["metrics"].items():
        colour = GREEN if m["ok"] else RED
        print(f"  {name:<14}{colour}{m['rate']:>9.0%}{RESET}  ≥{m['threshold']:.0%}"
              f"  {DIM}({m['passed']}/{m['total']}){RESET}")
    if regressions:
        print(f"\n{RED}{BOLD}Regressions vs baseline:{RESET}")
        for r in regressions:
            print(f"  {r['id']:<6} broke {', '.join(r['metrics'])}  {DIM}{r['question'][:60]}{RESET}")
    verdict = f"{GREEN}{BOLD}SHIP ✅{RESET}" if s["ship"] else f"{RED}{BOLD}BLOCK 🚫{RESET}"
    print(f"\nRelease gate: {verdict}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="v1", help="prompt version to evaluate (file in app/prompts/)")
    parser.add_argument("--compare", help="baseline version to diff against (uses saved results)")
    parser.add_argument("--replay", action="store_true", help="don't call the API; show saved results")
    args = parser.parse_args()

    if args.replay:
        result = runner.load(args.prompt)
        if not result:
            sys.exit(f"No saved results for {args.prompt}. Run without --replay first.")
    else:
        print(f"Running {len(runner.load_dataset())} cases against prompt {args.prompt}…")
        try:
            result = runner.run_eval(args.prompt, on_row=print_row)
        except llm.LLMError as e:
            sys.exit(f"{RED}Run failed:{RESET} {e}")

    regressions = None
    if args.compare:
        baseline = runner.load(args.compare)
        if not baseline:
            sys.exit(f"No saved results for baseline {args.compare}. Run it first.")
        regressions = runner.compare(baseline, result)

    print_report(result, regressions)
    sys.exit(0 if result["summary"]["ship"] else 1)


if __name__ == "__main__":
    main()
