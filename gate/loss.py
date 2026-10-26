import torch
import torch.nn.functional as F


def gate_loss(logits: torch.Tensor, targets: torch.Tensor, pairs: list[tuple[int, int]],
              margin: float = 1.0, lambda_rlcd: float = 0.5,
              class_weights: torch.Tensor | None = None) -> dict[str, torch.Tensor]:
    """
    C1: cross-entropy over (allow, block, escalate).
    C2: pairwise RLCD margin loss. For a matched pair (i, j) whose correct labels differ, each record's
        correct-label log-probability must beat the other record's score for that same label by `margin`:
          relu(m - (lp_i[y_i] - lp_j[y_i])) + relu(m - (lp_j[y_j] - lp_i[y_j]))
        i.e. the reward for choosing the right side of the contrast.
    """
    ce = F.cross_entropy(logits, targets, weight=class_weights)
    lp = F.log_softmax(logits, dim=-1)
    terms = []
    for i, j in pairs:
        yi, yj = targets[i], targets[j]
        if yi == yj:
            continue
        terms.append(F.relu(margin - (lp[i, yi] - lp[j, yi])) + F.relu(margin - (lp[j, yj] - lp[i, yj])))
    m = torch.stack(terms).mean() if terms else logits.new_zeros(())
    return {"total_loss": ce + lambda_rlcd * m, "ce_loss": ce, "margin_loss": m}
