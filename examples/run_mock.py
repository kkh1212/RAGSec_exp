"""Runnable offline example, using only this independent package."""
from rag_defense import DefenseEngine
from rag_defense.adapters.mock import build_scenario


def main():
    s = build_scenario("success")
    result = DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier).repair(s.request)
    print("SYNTHETIC ONLY:", result.status.value)
    print(result.final_response.text)
    print("Changed original characters:", result.changed_characters)
    print("Gold correctness:", result.gold_correctness)


if __name__ == "__main__":
    main()
