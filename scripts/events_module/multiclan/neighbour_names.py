"""
multiclan: names real neighbouring Clan cats in event and patrol text.

"""

import random
import re

from scripts.cat.cats import Cat
from scripts.cat.enums import CatAge, CatRank
from scripts.game_structure import game

APOS = "['’]"  # straight or curly apostrophe

_chosen = {}  # (group_ID, role) -> (moon, cat ID)


def name_neighbour_cats(text: str, other_clan) -> str:
    if not game.clan or game.clan.clancount != "multiclan":
        return text
    if other_clan not in game.clan.all_other_clans:
        return text

    # leaders, deputies and medicine cats: the Clan's actual ones
    for role, cat in (
        ("leader", other_clan.leader),
        ("deputy", other_clan.deputy),
        ("medicine cat", other_clan.medicine_cat),
    ):
        if not _alive_in(cat, other_clan):
            continue
        name = str(cat.name)
        # "o_c_n's leader's den" / "the o_c_n leader's den" -> "Ashstar's den"
        text = re.sub(
            rf"\b(?:the o_c_n|o_c_n{APOS}s) {role}{APOS}s\b",
            f"{name}'s",
            text,
            flags=re.IGNORECASE,
        )
        # "o_c_n's leader" / "the o_c_n leader" -> "Ashstar of o_c_n"
        # "o_c_n's deputy" / "the o_c_n deputy" -> "o_c_n's deputy Reedtail"
        replacement = (
            f"{name} of o_c_n" if role == "leader" else f"o_c_n's {role} {name}"
        )
        text = re.sub(
            rf"\b(?:the o_c_n|o_c_n{APOS}s) {role}\b(?!s)",
            replacement,
            text,
            flags=re.IGNORECASE,
        )

    # one warrior and one apprentice, named the first time they're mentioned
    for role in ("warrior", "apprentice"):
        if f"o_c_n {role}" not in text.lower():
            continue
        cat = _pick(other_clan, role)
        if not cat:
            continue
        name = str(cat.name)
        # "a o_c_n warrior" -> "a o_c_n warrior named Reedtail" (first time only)
        text, introduced = re.subn(
            rf"\b(an?) o_c_n {role}\b(?!s|{APOS}s)",
            rf"\1 o_c_n {role} named {name}",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
        # "the o_c_n warrior('s)" -> "Reedtail('s)"
        text = re.sub(
            rf"\bthe o_c_n {role}({APOS}s)?\b(?!s)",
            lambda m: name + ("'s" if m.group(1) else ""),
            text,
            flags=re.IGNORECASE,
        )

    return text


def _alive_in(cat, clan) -> bool:
    return bool(cat and not cat.dead and cat.status.group_ID == clan.group_ID)


def _pick(clan, role):
    """The neighbouring cat playing this role this moon."""
    key = (clan.group_ID, role)
    moon, cat_id = _chosen.get(key, (None, None))
    cat = Cat.fetch_cat(cat_id) if cat_id else None
    if moon == game.clan.age and _alive_in(cat, clan):
        return cat

    if role == "warrior":
        ranks = (CatRank.WARRIOR,)
    else:
        ranks = (CatRank.APPRENTICE,)
    options = [
        c
        for c in Cat.all_cats_list
        if _alive_in(c, clan)
        and c.status.rank in ranks
        and c.age not in (CatAge.NEWBORN, CatAge.KITTEN)
        and not c.not_working()
    ]
    if not options:
        return None
    cat = random.choice(options)
    _chosen[key] = (game.clan.age, cat.ID)
    return cat