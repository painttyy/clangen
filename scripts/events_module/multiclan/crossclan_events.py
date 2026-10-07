"""
multiclan: cross-clan relationship events.

Each moon, cats from different Clans (yours and your neighbours') run into
each other: friendships, rivalries, ambushes, and forbidden romances.

Uses GeneMod's cross-clan event files (resources/lang/en/events/
relationship_events/cross-clan_interactions/) and translates them on the fly
to work with official ClanGen, so the JSON files can stay identical to
GeneMod's and be updated from there.
"""

import copy
import random
import re

from scripts.cat.cats import Cat
from scripts.cat.enums import CatRank
from scripts.cat.skills import SkillPath
from scripts.clan import OtherClan
from scripts.clan_package.cotc import change_clan_relations
from scripts.config import get_config
from scripts.events_module.event_filters import (
    cat_for_event,
    check_rel_constraint_groups,
    event_for_cat,
    event_for_clan_relations,
)
from scripts.events_module.event_information import EventInformation
from scripts.events_module.text_pool_event.check_general_constraints import (
    passes_general_constraints,
)
from scripts.events_module.text_pool_event.handle_consequences import execute_outcome
from scripts.events_module.text_pool_event.text_pool_event import TextPoolEvent
from scripts.game_structure import game
from scripts.game_structure.localization import load_lang_resource

EVENT_FOLDER = "events/relationship_events/cross-clan_interactions"

# GeneMod renames some ranks; map them back to official ClanGen's names
STATUS_MAP = {
    "healer": CatRank.MEDICINE_CAT,
    "healer apprentice": CatRank.MEDICINE_APPRENTICE,
}
GENEMOD_ONLY_STATUSES = {"queen", "queen apprentice"}

_loaded = {}  # file path -> list of (TextPoolEvent, {abbr: clan number})
_used_this_moon = set()


# ---------------------------------------------------------------------------- #
#                                  main entry                                  #
# ---------------------------------------------------------------------------- #


def handle_crossclan_events():
    """Runs once per moon. Only does anything in multiclan mode."""
    if not game.clan or game.clan.clancount != "multiclan":
        return

    _used_this_moon.clear()
    viable = _viable_cats_by_clan()
    if len(viable) < 2:
        return

    average_size = sum(len(c) for c in viable.values()) / len(viable)
    event_count = min(
        get_config("multiclan.max_crossclan_events"), int(average_size / 2)
    )

    for _ in range(event_count):
        if len(viable) < 2:
            break
        main_clan = random.choice(list(viable))
        main_cat = random.choice(viable[main_clan])
        is_group = random.random() < 0.2
        _try_event(main_cat, main_clan, viable, is_group)


# ---------------------------------------------------------------------------- #
#                                   helpers                                    #
# ---------------------------------------------------------------------------- #


def _viable_cats_by_clan() -> dict:
    """{clan object: [cats who can take part]} for every Clan with eligible cats."""
    result = {}
    for clan in [game.clan] + game.clan.all_other_clans:
        if clan is game.clan:
            cats = [c for c in Cat.all_cats_list if c.status.alive_in_player_clan]
        else:
            cats = [
                c
                for c in Cat.all_cats_list
                if not c.dead and c.status.group_ID == clan.group_ID
            ]
        cats = [
            c
            for c in cats
            if not c.not_working()
            and c.status.rank not in (CatRank.NEWBORN, CatRank.KITTEN)
        ]
        if cats:
            result[clan] = cats
    return result


def _load_events(is_group: bool) -> list:
    folder = "group_interactions" if is_group else "normal_interactions"
    events = []
    for biome in ("general", game.clan.biome.lower()):
        path = f"{EVENT_FOLDER}/{folder}/{biome}.json"
        if path not in _loaded:
            _loaded[path] = _convert_file(path)
        events.extend(_loaded[path])
    return events


def _convert_file(path) -> list:
    """Loads a GeneMod cross-clan file and converts it for official ClanGen."""
    try:
        raw_events = load_lang_resource(path)
    except Exception:  # pylint: disable=broad-except
        return []  # missing biome file is fine
    converted = []
    for raw in raw_events or []:
        raw = copy.deepcopy(raw)
        # official ClanGen only supports two Clans per event here
        if raw.pop("nr_involved_clans", 2) > 2:
            continue
        raw.pop("other_clan_filter", None)
        raw.pop("involved_clans", None)

        clan_numbers = {}
        usable = True
        for abbr, info in raw.get("involved_cats", {}).items():
            clan_numbers[abbr] = info.pop("clan", 1 if abbr == "m_c" else 2)
            if "status" in info:
                info["status"] = _convert_statuses(info["status"])
                if info["status"] is None:
                    usable = False
            for holder in (info, info.get("stat", {})):
                if "skill" in holder:
                    holder["skill"] = _convert_skills(holder["skill"])
                    if holder["skill"] is None:
                        usable = False
        if not usable:
            continue
        try:
            converted.append((TextPoolEvent(**raw), clan_numbers))
        except TypeError as e:
            print(f"multiclan: skipped cross-clan event {raw.get('event_id')}: {e}")
    return converted


def _convert_statuses(statuses):
    """Translates GeneMod rank names. Returns None if nothing usable is left."""
    result = []
    for status in statuses:
        prefix = "-" if status.startswith("-") else ""
        name = status.lstrip("-")
        if name in GENEMOD_ONLY_STATUSES:
            continue
        result.append(prefix + str(STATUS_MAP.get(name, name)))
    if statuses and not result:
        return None
    return result


def _convert_skills(skills):
    """Drops skills that only exist in GeneMod. Returns None if nothing usable is left."""
    real_paths = {path.name for path in SkillPath}
    result = [s for s in skills if s.lstrip("-").split(",")[0] in real_paths]
    if skills and not result:
        return None
    return result


def _rep_clan(main_clan, other_clan):
    """The OtherClan whose relations with your Clan this event affects, if any."""
    if main_clan is game.clan and isinstance(other_clan, OtherClan):
        return other_clan
    if other_clan is game.clan and isinstance(main_clan, OtherClan):
        return main_clan
    return None  # two neighbouring Clans: your relations aren't affected


def _try_event(main_cat, main_clan, viable, is_group):
    other_clan = random.choice([c for c in viable if c is not main_clan])
    rep_clan = _rep_clan(main_clan, other_clan)

    candidates = []
    for event, clan_numbers in _load_events(is_group):
        if event.event_id in _used_this_moon:
            continue
        if event.required_reputation.get("other_clan") and not (
            rep_clan
            and event_for_clan_relations(
                event.required_reputation["other_clan"], rep_clan
            )
        ):
            continue
        if event.required_reputation and not rep_clan:
            continue
        if not passes_general_constraints(
            event, main_cat, {"m_c": main_cat}, other_clan=rep_clan
        ):
            continue
        if "war" in event.tags and not _at_war(rep_clan):
            continue
        if not event_for_cat(
            cat_info=event.involved_cats.get("m_c", {}),
            cat=main_cat,
            event_id=event.event_id,
        ):
            continue
        candidates.extend([(event, clan_numbers)] * event.weight)

    random.shuffle(candidates)
    tried = set()
    for event, clan_numbers in candidates:
        if event.event_id in tried:
            continue
        tried.add(event.event_id)
        involved = _find_cats(event, clan_numbers, main_cat, main_clan, other_clan, viable)
        if involved:
            _run_event(event, involved, main_clan, other_clan, rep_clan, viable)
            return


def _at_war(rep_clan) -> bool:
    war = getattr(game.clan, "war", None) or {}
    return bool(
        rep_clan and war.get("at_war") and war.get("enemy") == rep_clan.name
    )


def _find_cats(event, clan_numbers, main_cat, main_clan, other_clan, viable):
    """Picks the other cats for this event, or returns None if it can't."""
    involved = {"m_c": main_cat}
    for abbr, constraints in event.involved_cats.items():
        if abbr == "m_c":
            continue
        clan_number = clan_numbers.get(abbr, 2)
        if clan_number == "any":
            pool = viable[main_clan] + viable[other_clan]
        elif clan_number == 1:
            pool = viable[main_clan]
        else:
            pool = viable[other_clan]
        pool = [c for c in pool if c not in involved.values()]

        injuries = []
        for block in event.condition:
            if abbr in block.get("cats", []):
                injuries.extend(block.get("condition", []))

        options = cat_for_event(
            constraint_dict=constraints,
            possible_cats=pool,
            comparison_cat=main_cat,
            tags=event.tags,
            injuries=injuries,
            return_list=True,
            return_id=False,
        )
        if not options:
            return None
        random.shuffle(options)

        chosen = None
        for option in options:
            test = dict(involved)
            test[abbr] = option
            if all(
                check_rel_constraint_groups(block, test)
                for block in event.relationship_constraint
            ):
                chosen = option
                break
        if not chosen:
            return None
        involved[abbr] = chosen

    # relationship constraints between cats chosen above
    if not all(
        check_rel_constraint_groups(block, involved)
        for block in event.relationship_constraint
    ):
        return None
    return involved


def _fill_clan_names(text, main_clan, other_clan):
    if not isinstance(text, str):
        return text
    text = re.sub(r"\bo_c_n1\b", str(other_clan.name), text)
    text = re.sub(r"\bo_c_n\b", str(other_clan.name), text)
    text = re.sub(r"\bc_n\b", str(main_clan.name), text)
    return text


def _run_event(event, involved, main_clan, other_clan, rep_clan, viable):
    _used_this_moon.add(event.event_id)
    for cat in involved.values():
        for cats in viable.values():
            if cat in cats:
                cats.remove(cat)
    for clan in [c for c in viable if not viable[c]]:
        del viable[clan]

    # work on a copy so the loaded event isn't changed
    event = copy.deepcopy(event)
    event.strings = [_fill_clan_names(s, main_clan, other_clan) for s in event.strings]
    for block in event.condition:
        for key in ("scar_history", "death_history"):
            if key in block:
                block[key] = _fill_clan_names(block[key], main_clan, other_clan)
    for block in event.relationship_changes:
        for group, log in block.get("log", {}).items():
            block["log"][group] = _fill_clan_names(log, main_clan, other_clan)

    # reputation is handled here, since official ClanGen only knows your Clan's relations
    rep_change = event.reputation_changes.get("other_clan")
    event.reputation_changes = {}

    text, _results, _rel_results = execute_outcome(event, involved)

    if rep_change and rep_clan:
        change_clan_relations(rep_clan, rep_change)

    types = ["other_clans"]
    if main_clan is game.clan or other_clan is game.clan:
        types.append("relation")
    if event.condition:
        types.append("health")
    game.cur_events_list.append(EventInformation(text, types, cat_dict=involved))