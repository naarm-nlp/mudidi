"""OmniDocBench quick_match adapted for flat dictionary lines (one line = one unit)."""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

import Levenshtein

from mudidi.evaluation.stage1.tag_parser import casefold_letters_for_eval

LineIndex = int
PredKey = Tuple[int, ...]
MatchDict = Dict[int, Dict[str, object]]

HIGH_CONFIDENCE_NED = 0.25
REJECT_MATCH_NED = 0.7
FUZZY_SUBSET_NED = 0.4
MERGE_FUZZY_THRESHOLD = 0.6
MAX_ADJACENT_PRED_MERGE = 160


def grapheme_ned(pred: str, gold: str) -> float:
    """Normalised edit distance on casefolded grapheme clusters."""
    import grapheme

    pred_g = list(grapheme.graphemes(casefold_letters_for_eval(pred)))
    gold_g = list(grapheme.graphemes(casefold_letters_for_eval(gold)))
    if not pred_g and not gold_g:
        return 0.0
    max_len = max(len(pred_g), len(gold_g), 1)
    return Levenshtein.distance(pred_g, gold_g) / max_len


def build_cost_matrix(
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
) -> List[List[float]]:
    """Pairwise grapheme NED matrix (gold rows × pred cols)."""
    return [
        [grapheme_ned(norm_pred[j], norm_gold[i]) for j in range(len(norm_pred))]
        for i in range(len(norm_gold))
    ]


def _sub_pred_fuzzy_ned(gold: str, pred: str) -> float | None:
    """Minimum window NED when ``gold`` contains a window matching ``pred``."""
    gold_len = len(gold)
    pred_len = len(pred)
    if pred_len <= 0 or gold_len < pred_len:
        return None
    best = float("inf")
    for start in range(gold_len - pred_len + 1):
        window = gold[start : start + pred_len]
        best = min(best, grapheme_ned(pred, window))
    return best


def _sub_gt_fuzzy_ned(pred: str, gold: str) -> float:
    """Minimum window NED when ``pred`` contains a window matching ``gold``."""
    pred_len = len(pred)
    gold_len = len(gold)
    if gold_len <= 0 or pred_len < gold_len:
        return 1.0
    best = float("inf")
    for start in range(pred_len - gold_len + 1):
        window = pred[start : start + gold_len]
        best = min(best, grapheme_ned(gold, window))
    return best


def _judge_pred_merge(
    gold_line: str,
    pred_lines: Sequence[str],
    *,
    fuzzy_threshold: float = MERGE_FUZZY_THRESHOLD,
) -> tuple[bool, bool]:
    """Return (should_merge, continue_extending) for adjacent pred lines."""
    if len(pred_lines) <= 1:
        return False, False

    cur_pred = " ".join(pred_lines[:-1])
    merged_pred = " ".join(pred_lines)
    cur_dist = grapheme_ned(cur_pred, gold_line)
    merged_dist = grapheme_ned(merged_pred, gold_line)
    if merged_dist > cur_dist:
        return False, False

    for part in pred_lines[:-1]:
        part_dist = _sub_pred_fuzzy_ned(gold_line, part)
        if part_dist is None or part_dist > fuzzy_threshold:
            return False, False

    add_dist = _sub_pred_fuzzy_ned(gold_line, pred_lines[-1])
    if add_dist is None:
        return False, False

    merged_flag = add_dist < fuzzy_threshold
    continue_flag = len(merged_pred) <= len(gold_line)
    return merged_flag, continue_flag


def _merge_lists_with_sublists(
    main_list: List[LineIndex | List[LineIndex]],
    sub_lists: Sequence[Sequence[LineIndex]],
) -> List[LineIndex | List[LineIndex]]:
    result: List[LineIndex | List[LineIndex]] = list(copy.deepcopy(main_list))
    for sub in sub_lists:
        if not sub:
            continue
        pop_idx = result.index(sub[0])
        for _ in sub:
            result.pop(pop_idx)
        result.insert(pop_idx, list(sub))
    return result


def _get_final_subset(
    subsets: Sequence[Sequence[LineIndex]],
    costs: Sequence[float],
) -> List[List[LineIndex]]:
    """Pick non-overlapping pred-index merge windows (OmniDocBench helper)."""
    if not subsets or not costs:
        return []

    ranked = sorted(zip(subsets, costs), key=lambda item: item[0][0])
    groups: Dict[int, List[tuple[Sequence[LineIndex], float]]] = defaultdict(list)
    group_idx = 0
    groups[group_idx].append(ranked[0])

    for subset, cost in ranked[1:]:
        overlaps = any(
            idx in existing[0]
            for existing in groups[group_idx]
            for idx in subset
        )
        if overlaps:
            groups[group_idx].append((subset, cost))
        else:
            group_idx += 1
            groups[group_idx].append((subset, cost))

    final: List[List[LineIndex]] = []
    for group in groups.values():
        if len(group) == 1:
            final.append(list(group[0][0]))
            continue

        path_dict: Dict[int, List[tuple[Sequence[LineIndex], float]]] = {0: [group[0]]}
        for subset, cost in group[1:]:
            new_path = True
            for path_items in path_dict.values():
                is_dup = False
                is_same = False
                for path_item in path_items:
                    if path_item[0] == subset:
                        is_dup = is_same = True
                        if path_item[1] > cost:
                            path_items.remove(path_item)
                            path_items.append((subset, cost))
                    elif any(a == b for a in path_item[0] for b in subset):
                        is_dup = True
                if not is_dup:
                    path_items.append((subset, cost))
                    new_path = False
                if is_same:
                    new_path = False
            if new_path:
                path_dict[len(path_dict)] = [(subset, cost)]

        best_cost = float("inf")
        best_subset: List[List[LineIndex]] = []
        for path in path_dict.values():
            avg = sum(item[1] for item in path) / len(path)
            if avg < best_cost:
                best_cost = avg
                best_subset = [list(item[0]) for item in path]
        final.extend(best_subset)
    return final


def deal_with_truncated(
    cost_matrix: Sequence[Sequence[float]],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
) -> tuple[List[List[float]], List[str], List[LineIndex | List[LineIndex]]]:
    """Merge adjacent pred lines to improve match (Adjacency Search Match)."""
    n_gold = len(norm_gold)
    n_pred = len(norm_pred)
    if n_gold == 0 or n_pred == 0:
        return [list(row) for row in cost_matrix], list(norm_pred), list(range(n_pred))

    matched = [
        (i, j)
        for i in range(n_gold)
        for j in range(n_pred)
        if cost_matrix[i][j] < HIGH_CONFIDENCE_NED
    ]
    masked_gold = {i for i, _ in matched}
    masked_pred = {j for _, j in matched}
    unmasked_gold = [i for i in range(n_gold) if i not in masked_gold]
    unmasked_pred = [j for j in range(n_pred) if j not in masked_pred]

    merge_info: Dict[int, Dict[str, object]] = {}
    for gt_idx in unmasked_gold:
        candidates: List[List[LineIndex]] = []
        merged_dists: List[float] = []
        for pred_idx in unmasked_pred:
            step = 1
            merged_parts = [norm_pred[pred_idx]]
            while True:
                if step >= MAX_ADJACENT_PRED_MERGE:
                    break
                if pred_idx + step in masked_pred or pred_idx + step >= n_pred:
                    break
                merged_parts.append(norm_pred[pred_idx + step])
                ok, cont = _judge_pred_merge(norm_gold[gt_idx], merged_parts)
                if not ok:
                    break
                step += 1
                if not cont:
                    break
            window = list(range(pred_idx, pred_idx + step))
            candidates.append(window)
            merged_text = " ".join(norm_pred[i] for i in window)
            merged_dists.append(grapheme_ned(merged_text, norm_gold[gt_idx]))

        if merged_dists:
            min_idx = int(min(range(len(merged_dists)), key=merged_dists.__getitem__))
            merge_info[gt_idx] = {
                "subset_certain": candidates[min_idx],
                "min_cost": merged_dists[min_idx],
            }
        else:
            merge_info[gt_idx] = {"subset_certain": [], "min_cost": float("inf")}

    certain = [
        merge_info[i]["subset_certain"]
        for i in unmasked_gold
        if merge_info[i]["subset_certain"]
    ]
    certain_costs = [
        merge_info[i]["min_cost"]
        for i in unmasked_gold
        if merge_info[i]["subset_certain"]
    ]
    final_subsets = _get_final_subset(certain, certain_costs)
    if not final_subsets:
        return [list(row) for row in cost_matrix], list(norm_pred), list(range(n_pred))

    pred_index_map = _merge_lists_with_sublists(list(range(n_pred)), final_subsets)
    merged_pred_lines = [
        " ".join(norm_pred[item] for item in ([idx] if isinstance(idx, int) else idx))
        for idx in pred_index_map
    ]
    new_matrix = build_cost_matrix(norm_gold, merged_pred_lines)
    return new_matrix, merged_pred_lines, pred_index_map


def cal_final_match(
    cost_matrix: Sequence[Sequence[float]],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
) -> tuple[List[LineIndex | List[LineIndex]], List[int], List[float]]:
    """Run adjacency merge then Hungarian assignment."""
    new_matrix, merged_pred, pred_index_map = deal_with_truncated(
        cost_matrix, norm_gold, norm_pred
    )
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    arr = np.array(new_matrix, dtype=float)
    row_ind, col_ind = linear_sum_assignment(arr)
    cost_list = [float(new_matrix[r][c]) for r, c in zip(row_ind, col_ind)]
    matched_cols = [pred_index_map[c] for c in col_ind]
    return matched_cols, list(row_ind), cost_list


def process_matches(
    matched_cols: Sequence[LineIndex | List[LineIndex]],
    row_ind: Sequence[int],
    cost_list: Sequence[float],
    norm_gold: Sequence[str],
) -> tuple[MatchDict, List[int], List[LineIndex]]:
    """Accept Hungarian pairs; reject weak matches (NED > 0.7)."""
    matches: MatchDict = {}
    unmatched_gold: List[int] = []
    unmatched_pred: List[LineIndex] = []

    for gt_idx in range(len(norm_gold)):
        if gt_idx not in row_ind:
            unmatched_gold.append(gt_idx)
            continue
        pos = list(row_ind).index(gt_idx)
        pred_key = matched_cols[pos]
        edit = cost_list[pos]

        if pred_key is None:
            unmatched_gold.append(gt_idx)
            continue

        if isinstance(pred_key, list):
            pred_range = list(range(pred_key[0], pred_key[-1] + 1))
        else:
            pred_range = [pred_key]

        if edit > REJECT_MATCH_NED:
            unmatched_gold.append(gt_idx)
            unmatched_pred.extend(pred_range)
            continue

        matches[gt_idx] = {"pred_indices": pred_range, "edit_distance": edit}
        for idx in pred_range:
            if idx in unmatched_pred:
                unmatched_pred.remove(idx)

    return matches, unmatched_gold, unmatched_pred


def fuzzy_match_unmatched(
    unmatched_gold: Sequence[int],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
    *,
    fuzzy_threshold: float = FUZZY_SUBSET_NED,
) -> Dict[LineIndex, List[int]]:
    """Map unmatched pred lines to gold lines via fuzzy subset search."""
    matching: Dict[LineIndex, List[int]] = {}
    for pred_idx, pred_text in enumerate(norm_pred):
        hits: List[int] = []
        for gt_idx in unmatched_gold:
            dist = _sub_gt_fuzzy_ned(pred_text, norm_gold[gt_idx])
            if dist < fuzzy_threshold:
                hits.append(gt_idx)
        if hits:
            matching[pred_idx] = hits
    return matching


def merge_matches(
    matches: MatchDict,
    fuzzy: Dict[LineIndex, List[int]],
) -> Dict[PredKey, Dict[str, object]]:
    """Combine Hungarian and fuzzy maps; allow many gold lines → one pred."""
    final: Dict[PredKey, Dict[str, object]] = {}
    processed_gold: set[int] = set()

    for gt_idx, info in matches.items():
        pred_key = tuple(sorted(info["pred_indices"]))  # type: ignore[arg-type]
        if pred_key in final:
            if gt_idx not in processed_gold:
                final[pred_key]["gt_indices"].append(gt_idx)  # type: ignore[union-attr]
                processed_gold.add(gt_idx)
        else:
            final[pred_key] = {
                "gt_indices": [gt_idx],
                "edit_distance": info["edit_distance"],
            }
            processed_gold.add(gt_idx)

    for pred_idx, gt_indices in fuzzy.items():
        pred_key = (pred_idx,)
        if pred_key in final:
            for gt_idx in gt_indices:
                if gt_idx not in processed_gold:
                    final[pred_key]["gt_indices"].append(gt_idx)  # type: ignore[union-attr]
                    processed_gold.add(gt_idx)
        else:
            new_gt = [g for g in gt_indices if g not in processed_gold]
            if new_gt:
                final[pred_key] = {"gt_indices": new_gt, "edit_distance": None}
                processed_gold.update(new_gt)
    return final


def recalculate_edit_distances(
    final_matches: Dict[PredKey, Dict[str, object]],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
) -> None:
    """Recompute NED after gold/pred merges."""
    for pred_key, info in final_matches.items():
        gt_indices = sorted(set(info["gt_indices"]))  # type: ignore[arg-type]
        if not gt_indices:
            info["edit_distance"] = 1.0
            continue

        pred_text = " ".join(
            norm_pred[idx] for idx in pred_key if isinstance(idx, int)
        )
        if len(gt_indices) > 1:
            gold_text = "".join(norm_gold[i] for i in gt_indices)
        else:
            gold_text = norm_gold[gt_indices[0]]
        info["edit_distance"] = grapheme_ned(pred_text, gold_text)


def convert_final_matches(
    final_matches: Dict[PredKey, Dict[str, object]],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
) -> List[Dict[str, object]]:
    """Expand final match dict into per-(gold,pred) entries."""
    converted: List[Dict[str, object]] = []
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()

    for pred_key, info in final_matches.items():
        pred_text = " ".join(norm_pred[i] for i in pred_key if isinstance(i, int))
        for gt_idx in sorted(set(info["gt_indices"])):  # type: ignore[arg-type]
            converted.append(
                {
                    "gt_idx": gt_idx,
                    "pred_idx": list(pred_key),
                    "edit": info["edit_distance"],
                    "norm_gt": norm_gold[gt_idx],
                    "norm_pred": pred_text,
                }
            )
            matched_gold.add(gt_idx)
            matched_pred.update(i for i in pred_key if isinstance(i, int))

    unmatched_gold = sorted(set(range(len(norm_gold))) - matched_gold)
    unmatched_pred = sorted(set(range(len(norm_pred))) - matched_pred)

    if unmatched_pred:
        if unmatched_gold:
            sub_matrix = build_cost_matrix(
                [norm_gold[i] for i in unmatched_gold],
                [norm_pred[i] for i in unmatched_pred],
            )
            import numpy as np
            from scipy.optimize import linear_sum_assignment

            row_ind, col_ind = linear_sum_assignment(np.array(sub_matrix))
            for row, col in zip(row_ind, col_ind):
                converted.append(
                    {
                        "gt_idx": unmatched_gold[row],
                        "pred_idx": [unmatched_pred[col]],
                        "edit": 1.0,
                        "norm_gt": norm_gold[unmatched_gold[row]],
                        "norm_pred": norm_pred[unmatched_pred[col]],
                    }
                )
                matched_gold.add(unmatched_gold[row])
        else:
            converted.append(
                {
                    "gt_idx": None,
                    "pred_idx": unmatched_pred,
                    "edit": 1.0,
                    "norm_gt": "",
                    "norm_pred": " ".join(norm_pred[i] for i in unmatched_pred),
                }
            )
    for gt_idx in sorted(set(range(len(norm_gold))) - matched_gold):
        converted.append(
            {
                "gt_idx": gt_idx,
                "pred_idx": [],
                "edit": 1.0,
                "norm_gt": norm_gold[gt_idx],
                "norm_pred": "",
            }
        )
    return converted


def merge_duplicates_add_unmatched(
    converted: Sequence[Dict[str, object]],
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
    gold_tagged: Sequence[str],
    pred_tagged: Sequence[str],
) -> List[Dict[str, object]]:
    """Merge entries sharing the same pred key; append missing gold lines."""
    merged: List[Dict[str, object]] = []
    processed_pred: set[PredKey] = set()
    processed_gold: set[int] = set()

    for entry in converted:
        pred_key = tuple(entry["pred_idx"]) if entry["pred_idx"] else tuple()
        if pred_key in processed_pred or not pred_key:
            continue
        merged_entry = {
            "gold_indices": [entry["gt_idx"]],
            "pred_indices": list(pred_key),
            "edit": entry["edit"],
        }
        for other in converted:
            other_key = tuple(other["pred_idx"]) if other["pred_idx"] else tuple()
            if other_key == pred_key and other is not entry and other["gt_idx"] is not None:
                merged_entry["gold_indices"].append(other["gt_idx"])
                processed_gold.add(other["gt_idx"])  # type: ignore[arg-type]
        merged.append(merged_entry)
        processed_pred.add(pred_key)
        if entry["gt_idx"] is not None:
            processed_gold.add(entry["gt_idx"])  # type: ignore[arg-type]

    results: List[Dict[str, object]] = []
    for entry in merged:
        gold_indices = sorted({i for i in entry["gold_indices"] if i is not None})
        pred_indices = entry["pred_indices"]
        norm_gt = " ".join(norm_gold[i] for i in gold_indices)
        tagged_gt = " ".join(gold_tagged[i] for i in gold_indices)
        norm_p = " ".join(norm_pred[i] for i in pred_indices)
        tagged_p = " ".join(pred_tagged[i] for i in pred_indices)
        edit = entry["edit"]
        if edit is None:
            edit = grapheme_ned(norm_p, norm_gt)
        results.append(
            {
                "gold_indices": gold_indices,
                "pred_indices": pred_indices,
                "norm_gt": norm_gt,
                "norm_pred": norm_p,
                "tagged_gt": tagged_gt,
                "tagged_pred": tagged_p,
                "edit": float(edit),
            }
        )

    for gt_idx in range(len(norm_gold)):
        if gt_idx not in processed_gold:
            results.append(
                {
                    "gold_indices": [gt_idx],
                    "pred_indices": [],
                    "norm_gt": norm_gold[gt_idx],
                    "norm_pred": "",
                    "tagged_gt": gold_tagged[gt_idx],
                    "tagged_pred": "",
                    "edit": 1.0,
                }
            )
    return results


def quick_match_lines(
    norm_gold: Sequence[str],
    norm_pred: Sequence[str],
    gold_tagged: Sequence[str],
    pred_tagged: Sequence[str],
) -> List[Dict[str, object]]:
    """Run OmniDocBench quick_match with one flat line per alignment unit."""
    if not norm_gold and not norm_pred:
        return []
    if not norm_gold:
        return [
            {
                "gold_indices": [],
                "pred_indices": list(range(len(norm_pred))),
                "norm_gt": "",
                "norm_pred": " ".join(norm_pred),
                "tagged_gt": "",
                "tagged_pred": " ".join(pred_tagged),
                "edit": 1.0,
            }
        ]
    if not norm_pred:
        return [
            {
                "gold_indices": [i],
                "pred_indices": [],
                "norm_gt": norm_gold[i],
                "norm_pred": "",
                "tagged_gt": gold_tagged[i],
                "tagged_pred": "",
                "edit": 1.0,
            }
            for i in range(len(norm_gold))
        ]
    if len(norm_gold) == 1 and len(norm_pred) == 1:
        edit = grapheme_ned(norm_pred[0], norm_gold[0])
        return [
            {
                "gold_indices": [0],
                "pred_indices": [0],
                "norm_gt": norm_gold[0],
                "norm_pred": norm_pred[0],
                "tagged_gt": gold_tagged[0],
                "tagged_pred": pred_tagged[0],
                "edit": edit,
            }
        ]

    cost_matrix = build_cost_matrix(norm_gold, norm_pred)
    matched_cols, row_ind, cost_list = cal_final_match(
        cost_matrix, norm_gold, norm_pred
    )
    matches, unmatched_gold, _unmatched_pred = process_matches(
        matched_cols, row_ind, cost_list, norm_gold
    )
    fuzzy = fuzzy_match_unmatched(unmatched_gold, norm_gold, norm_pred)
    final = merge_matches(matches, fuzzy)
    recalculate_edit_distances(final, norm_gold, norm_pred)
    converted = convert_final_matches(final, norm_gold, norm_pred)
    return merge_duplicates_add_unmatched(
        converted, norm_gold, norm_pred, gold_tagged, pred_tagged
    )
