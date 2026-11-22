"""Group variants of one word into families.

    python scripts/build_families.py            # show what would change
    python scripts/build_families.py --apply    # write family_id and groups

Variants of one word share a family_id, which is the rank of the most frequent
member. The app then shows the family as a single entry that still carries every
variant's own rank and frequency.

Run this again after importing new words; it only reports and writes families it
can justify, and never touches a word that is already in a family.
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.store import get_store  # noqa: E402

# A gloss describing a conjugated verb. Verb forms are never number/gender
# variants of each other, so words glossed this way stay on their own.
VERBISH = re.compile(
    r"^(to )|"
    r"^(i|you|we|they|he|she|it|he/she)\b|"
    r"\b(will|would|was|were)\b|"
    r"\((subj\.|imperative|preterite|imperfect|p\.p\.|formal|pl\. subj\.|pl\. imperative)",
    re.IGNORECASE,
)

# Shortened forms are their own word: 'buen' does not join 'bueno'.
APOCOPATED = {
    "buen", "mal", "gran", "primer", "tercer", "san", "algún", "algun",
    "ningún", "ningun", "cualquier", "cien", "tan", "recién", "recien",
}

# Words that share a stem with a family without being a variant of it: 'ray' is
# a person's name, 'plano' is the blueprint sense, 'dr'/'dra' are abbreviations,
# 'des' is a bad entry, and 'trabaja' is the verb, not a plural of 'trabajo'.
NEVER_MERGE = {"ray", "plano", "dr", "dra", "des", "trabaja"}

# When members disagree, the group needing the most study wins.
STUDY_ORDER = ["4", "3", "2", "1", "0a", "0b", "n", "v"]

# Families ruled against that default, keyed by their most frequent member.
GROUP_OVERRIDES = {"malo": "0a"}

# Gerunds whose stem changes cannot be recovered by removing -ando/-iendo.
IRREGULAR_GERUNDS = {
    "diciendo": "decir",
    "durmiendo": "dormir",
    "muriendo": "morir",
    "mintiendo": "mentir",
    "siguiendo": "seguir",
    "pidiendo": "pedir",
    "viniendo": "venir",
    "leyendo": "leer",
    "cayendo": "caer",
    "yendo": "ir",
}

# Irregular participles present in the source list. Lexicalized forms such as
# hecho ("fact/done") and puesto ("position/put") are deliberately omitted.
IRREGULAR_PARTICIPLES = {
    "sido": "ser",
    "ido": "ir",
    "visto": "ver",
    "dicho": "decir",
    "vuelto": "volver",
    "escrito": "escribir",
    "cubierto": "cubrir",
    "descubierto": "descubrir",
}

# These look like participles but their listed meaning is a noun or adjective,
# so they remain independent words rather than joining the verb.
LEXICALIZED_PARTICIPLES = {
    "cuidado", "pasado", "sentido", "dormido", "oído", "querido",
    "partido", "pedido", "parecido", "casado", "preocupado", "pesado",
    "cumplido", "estado", "hecho", "muerto", "puesto", "abierto", "roto",
    "conocido", "disparado",
}

CLITIC = r"(?:se|me|te|nos|os|lo|la|los|las|le|les)"


def is_nominal(word):
    if word.get("group_code") == "v":
        return False
    return not VERBISH.search(word["english"] or "")


def gloss_senses(english):
    """Base meanings of a gloss, ignoring (fem.)/(pl.) annotations.

    'black (fem.)' -> {'black'};  'movies' -> {'movies', 'movie'}
    """
    text = re.sub(r"\([^)]*\)", " ", (english or "").lower())
    senses = set()
    for part in re.split(r"[/;,]", text):
        part = part.strip()
        if not part:
            continue
        senses.add(part)
        if part.endswith("es") and len(part) > 4:
            senses.add(part[:-2])
        if part.endswith("s") and len(part) > 3:
            senses.add(part[:-1])
    return senses


def stem(spanish):
    """Shared key for -o/-a/-os/-as and singular/plural variants.

    Accents are kept, so 'cuanto' and 'cuánto' never land in one family.
    """
    word = spanish.lower()
    for suffix in ("os", "as"):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)]
    if word.endswith("ces") and len(word) > 4:
        return word[:-3] + "z"
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    for suffix in ("a", "o", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)]
    return word


def find_nominal_families(words):
    eligible = [w for w in words
                if is_nominal(w)
                and w["spanish"].lower() not in APOCOPATED
                and w["spanish"].lower() not in NEVER_MERGE]

    buckets = {}
    for word in eligible:
        buckets.setdefault(stem(word["spanish"]), []).append(word)

    # A shared stem is not enough on its own: it pairs casa with caso and dios
    # with día. Split each bucket into runs that also agree on what they mean.
    families = []
    for members in buckets.values():
        members.sort(key=lambda w: w["rank"])
        runs = []
        for word in members:
            senses = gloss_senses(word["english"])
            for run in runs:
                if run["senses"] & senses:
                    run["words"].append(word)
                    run["senses"] |= senses
                    break
            else:
                runs.append({"senses": senses, "words": [word]})
        families.extend(run["words"] for run in runs if len(run["words"]) > 1)

    families.sort(key=lambda members: members[0]["rank"])
    return families


def normalized(text):
    """Lowercase without stress marks, preserving ñ as a different letter."""
    import unicodedata

    protected = text.lower().replace("ñ", "\0")
    return "".join(
        char for char in unicodedata.normalize("NFD", protected)
        if unicodedata.category(char) != "Mn"
    ).replace("\0", "ñ")


def infinitive_lemma(word):
    """Return the bare infinitive, removing one or two attached pronouns."""
    if not (word["english"] or "").lower().startswith("to "):
        return None
    form = normalized(word["spanish"])
    if form == "ir" or re.fullmatch(r"ir" + CLITIC + r"{1,2}", form):
        return "ir"
    match = re.fullmatch(r"(.+(?:ar|er|ir))" + CLITIC + r"{0,2}", form)
    return match.group(1) if match else None


def gerund_lemmas(spanish):
    """Possible infinitives for a gerund; only a present lemma is accepted."""
    form = normalized(spanish)
    form = re.sub(CLITIC + r"{1,2}$", "", form)
    if form in IRREGULAR_GERUNDS:
        return [IRREGULAR_GERUNDS[form]]
    if form.endswith("ando") and len(form) > 5:
        return [form[:-4] + "ar"]
    if form.endswith("iendo") and len(form) > 5:
        base = form[:-5]
        return [base + "er", base + "ir"]
    return []


def participle_lemmas(spanish):
    """Possible infinitives for a regular or explicitly mapped participle."""
    form = normalized(spanish)
    if form in IRREGULAR_PARTICIPLES:
        return [IRREGULAR_PARTICIPLES[form]]
    # Only the invariant masculine singular form belongs to a compound-tense
    # verb family. Feminine/plural forms are adjectival and stay independent.
    match = re.fullmatch(r"(.+?)ado", form)
    if match:
        return [match.group(1) + "ar"]
    match = re.fullmatch(r"(.+?)ido", form)
    if match:
        return [match.group(1) + "er", match.group(1) + "ir"]
    return []


def find_verb_families(words):
    """Find infinitive + clitic + gerund + non-lexicalized participle families."""
    by_lemma = {}
    for word in words:
        lemma = infinitive_lemma(word)
        if lemma:
            by_lemma.setdefault(lemma, []).append(word)

    # A participle already grouped with gender/number variants is functioning
    # as a noun or adjective in this list, so do not absorb that family.
    stored_families = {}
    for word in words:
        if word.get("family_id") is not None:
            stored_families.setdefault(word["family_id"], []).append(word)

    for word in words:
        candidates = gerund_lemmas(word["spanish"])
        is_gerund = bool(candidates)
        if not candidates:
            candidates = participle_lemmas(word["spanish"])
            form = word["spanish"].lower()
            if not candidates or form in LEXICALIZED_PARTICIPLES:
                continue

        matches = [lemma for lemma in candidates if lemma in by_lemma]
        if not is_gerund and len(matches) == 1:
            # Existing number/gender families indicate an adjectival use.
            # Keep a participle only if its stored family already contains this
            # infinitive, which makes repeated runs idempotent.
            stored = stored_families.get(word.get("family_id"), [])
            if len(stored) > 1 and not any(
                infinitive_lemma(member) == matches[0] for member in stored
            ):
                continue
        if len(matches) == 1:
            by_lemma[matches[0]].append(word)
        elif is_gerund and len(matches) > 1:
            # This would be linguistically ambiguous; require an explicit map.
            continue

    families = []
    for members in by_lemma.values():
        unique = {word["rank"]: word for word in members}
        if len(unique) > 1:
            families.append(sorted(unique.values(), key=lambda word: word["rank"]))
    families.sort(key=lambda members: members[0]["rank"])
    return families


def combine_families(words, candidate_families):
    """Union overlapping nominal and verb families into valid components."""
    parent = {word["rank"]: word["rank"] for word in words}

    def root(rank):
        while parent[rank] != rank:
            parent[rank] = parent[parent[rank]]
            rank = parent[rank]
        return rank

    def union(left, right):
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for members in candidate_families:
        first = members[0]["rank"]
        for member in members[1:]:
            union(first, member["rank"])

    components = {}
    by_rank = {word["rank"]: word for word in words}
    for rank in parent:
        components.setdefault(root(rank), []).append(by_rank[rank])
    families = [
        sorted(members, key=lambda word: word["rank"])
        for members in components.values() if len(members) > 1
    ]
    families.sort(key=lambda members: members[0]["rank"])
    return families


def resolve_group(members):
    """The single group a merged family carries."""
    override = GROUP_OVERRIDES.get(members[0]["spanish"].lower())
    if override:
        return override
    present = {w["group_code"] for w in members if w.get("group_code")}
    for group in STUDY_ORDER:
        if group in present:
            return group
    return None


def describe(members):
    return " · ".join("%s(#%d%s)" % (w["spanish"], w["rank"],
                                     "" if not w.get("group_code") else " " + w["group_code"])
                      for w in members)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write the changes.")
    parser.add_argument("--show", type=int, default=15, help="How many families to print.")
    args = parser.parse_args()

    store = get_store()
    words = store.list_words()
    nominal_families = find_nominal_families(words)
    verb_families = find_verb_families(words)
    families = combine_families(words, nominal_families + verb_families)

    new = [m for m in families if not any(w.get("family_id") for w in m)]
    conflicts = [m for m in families
                 if len({w["group_code"] for w in m if w.get("group_code")}) > 1]

    print("words: %d | families: %d | words in a family: %d | list becomes %d entries"
          % (len(words), len(families), sum(len(m) for m in families),
             len(words) - sum(len(m) - 1 for m in families)))
    print("families not yet stored: %d | group conflicts resolved: %d"
          % (len(new), len(conflicts)))
    print("nominal candidates: %d | verb candidates: %d"
          % (len(nominal_families), len(verb_families)))
    print("conflicts resolve to:", dict(Counter(resolve_group(m) for m in conflicts)))
    print()

    for members in families[: args.show]:
        print(describe(members), "->", resolve_group(members))
    if len(families) > args.show:
        print("... %d more" % (len(families) - args.show))

    if not args.apply:
        print("\nDRY RUN - pass --apply to write")
        return

    # Words already carrying a family_id were merged by an earlier run, merged
    # by hand, or split apart by hand (a family of one), so leave them alone.
    regroup = {}
    for members in new:
        ranks = [w["rank"] for w in members]
        store.set_family(ranks, min(ranks))
        group = resolve_group(members)
        if group:
            regroup.setdefault(group, []).extend(
                w["rank"] for w in members if w.get("group_code") != group
            )

    # One write per group rather than one per family: against Supabase that is
    # the difference between a handful of requests and several hundred.
    for group, ranks in regroup.items():
        store.assign(ranks, group)

    print("\nwrote %d new families, regrouped %d words"
          % (len(new), sum(len(r) for r in regroup.values())))


if __name__ == "__main__":
    main()
