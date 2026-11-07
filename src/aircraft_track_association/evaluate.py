"""Exact directed successor-link metrics."""


def link_metrics(predicted: dict[str, str], truth: dict[str, str | None]) -> dict[str, float]:
    pred = set(predicted.items())
    true = {(s, d) for s, d in truth.items() if d is not None}
    tp = len(pred & true)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(true) if true else 0.0
    f1 = 2 * precision * recall / (precision + recall) if tp else 0.0
    ends = [s for s, d in truth.items() if d is None]
    return {
        "true_links": len(true),
        "predicted_links": len(pred),
        "correct_links": tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "correct_no_successor": sum(s not in predicted for s in ends),
        "no_successor": len(ends),
    }
