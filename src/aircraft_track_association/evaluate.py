"""Score predicted successor links against labels, counting exact directed links only.

A predicted link is correct only if it names the labelled immediate successor; a link to a
later tracklet of the same aircraft (skipping one) is wrong. Two kinds of error are kept apart
because they do not cost the same: a wrong link asserts a false identity, while an unreported
link only withholds a true one.
- wrong: predicted links that are not the labelled successor (false positives).
- unreported: labelled links whose source got no prediction at all (abstentions).
A true link can also be lost because its source was given a wrong link; that counts as wrong.
"""


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
        "wrong_links": len(pred) - tp,
        "unreported_links": sum(s not in predicted for s, _ in true),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "correct_no_successor": sum(s not in predicted for s in ends),
        "no_successor": len(ends),
    }
