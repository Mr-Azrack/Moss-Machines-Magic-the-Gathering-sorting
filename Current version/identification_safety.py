"""Fail-closed card-identification selection for automated sorting.

Pure-Python logic kept separate from camera/DB code for regression testing.
A similarity score is not a calibrated probability of the product ID being correct.
"""


def choose_safe_match(matches, *, min_phash_confidence=85.0, min_gap=4.0,
                      symbol_set=None, symbol_confidence=0.0, symbol_scores=None,
                      symbol_min_confidence=0.50, symbol_gap=0.08):
    """Return (candidate, reason) or (None, reason) for an unsafe identification.

    The set symbol may select only a *real* same-name product candidate; it may
    never change the set label of a different product ID. A symbol becomes a
    tiebreaker only when its top score clearly beats another evaluated set.
    """
    ranked = sorted(matches or [], key=lambda m: float(m.get('distance', 999)))
    if not ranked:
        return None, 'no_candidates'

    top = ranked[0]
    if not top.get('product_id') or not top.get('name') or top.get('name') == 'Unknown' or not top.get('set'):
        return None, 'missing_product_identity'

    def ident(m):
        return str(m.get('product_id', '')).strip()

    def card_set(m):
        return str(m.get('set') or '').strip().upper()

    def card_name(m):
        return str(m.get('name') or '').strip().casefold()

    def quality(m):
        return float(m.get('phash_confidence', m.get('confidence', 0)) or 0)

    # The MSER-blended display confidence is deliberately NOT used here.
    chosen = top
    scores = {str(k).upper(): float(v) for k, v in (symbol_scores or {}).items()}
    sym = str(symbol_set or '').upper()
    symbol_decisive = False
    if sym and sym in scores and len(scores) >= 2 and float(symbol_confidence) >= symbol_min_confidence:
        others = [v for k, v in scores.items() if k != sym]
        decisive = bool(others) and float(symbol_confidence) - max(others) >= symbol_gap
        if decisive:
            symbol_decisive = True
            options = [m for m in ranked if card_name(m) == card_name(top) and card_set(m) == sym]
            if not options:
                return None, 'symbol_printing_not_in_candidates'
            chosen = options[0]
            # A strong set symbol is not evidence that a totally different
            # picture/name is correct, or permission to accept a distant pHash.
            if float(chosen['distance']) - float(top['distance']) > 10.0:
                return None, 'symbol_phash_disagreement'

    if not chosen.get('product_id') or not chosen.get('set') or quality(chosen) < min_phash_confidence:
        return None, 'low_phash_confidence_or_missing_identity'

    # A decisive set symbol can disambiguate different sets of the *same*
    # named card. It cannot disambiguate two products with the same set/name,
    # nor two similar-looking cards with different names.
    contenders = []
    for m in ranked:
        if ident(m) == ident(chosen):
            continue
        if symbol_decisive and card_name(m) == card_name(chosen) and card_set(m) != card_set(chosen):
            continue  # The set symbol independently distinguishes this printing.
        contenders.append(m)
    contenders.sort(key=lambda m: float(m.get('distance', 999)))
    if contenders:
        margin = float(contenders[0]['distance']) - float(chosen['distance'])
        if margin < min_gap:
            return None, 'ambiguous_phash_candidates'

    return chosen, 'verified'
