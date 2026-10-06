"""
multiclan: monthly upkeep for the neighbouring Clans.

Keeps each Other Clan running on its own: replaces leaders, deputies and
medicine cats who die, leave or retire, and gives the Clans new litters
of kits so they don't dwindle away.
"""

import random

from scripts.cat.cats import Cat
from scripts.cat.enums import CatAge, CatRank
from scripts.cat.factories.new_cat_factory import NewCatFactory
from scripts.cat_relations.cat_handle_funcs import create_relationships_new_cat
from scripts.config import get_config
from scripts.events_module.event_information import EventInformation
from scripts.game_structure import game

ADULT_AGES = (CatAge.YOUNG_ADULT, CatAge.ADULT, CatAge.SENIOR_ADULT)


def handle_other_clans():
    """Runs once per moon. Only does anything in multiclan mode."""
    if not game.clan or game.clan.clancount != "multiclan":
        return

    for clan in game.clan.all_other_clans:
        members = get_members(clan)
        if not members:
            continue  # this Clan has died out

        for cat in members:
            if cat.birth_cooldown:
                cat.birth_cooldown -= 1

        handle_succession(clan, members)
        handle_litters(clan, members)


def get_members(clan) -> list:
    """All living cats currently in this Clan."""
    return [
        c
        for c in Cat.all_cats_list
        if not c.dead and c.status.group_ID == clan.group_ID
    ]


def still_holds(cat, clan, rank) -> bool:
    """True if the cat is alive, still in this Clan, and still has this rank."""
    return (
        cat is not None
        and not cat.dead
        and cat.status.group_ID == clan.group_ID
        and cat.status.rank == rank
    )


def close_family(cat_a, cat_b) -> bool:
    """True if the two cats are parent/child or share a parent."""
    parents_a = {p for p in (cat_a.parent1, cat_a.parent2) if p}
    parents_b = {p for p in (cat_b.parent1, cat_b.parent2) if p}
    return (
        cat_a.ID in parents_b
        or cat_b.ID in parents_a
        or bool(parents_a & parents_b)
    )


def set_rank(cat, rank):
    """Changes an Other Clan cat's rank without touching the player Clan."""
    cat.status._change_rank(rank)  # pylint: disable=protected-access
    cat.name.status = rank


def other_clan_event(text, cat):
    game.cur_events_list.append(
        EventInformation(text, ["ceremony", "other_clans"], cat_dict={"m_c": cat})
    )


def handle_succession(clan, members):
    warriors = [
        c for c in members if c.status.rank == CatRank.WARRIOR and c.age in ADULT_AGES
    ]

    # LEADER
    if not still_holds(clan.leader, clan, CatRank.LEADER):
        if still_holds(clan.deputy, clan, CatRank.DEPUTY):
            new_leader = clan.deputy
            clan.deputy = None
        elif warriors:
            new_leader = random.choice(warriors)
        else:
            new_leader = None

        if new_leader:
            if new_leader in warriors:
                warriors.remove(new_leader)
            old_name = str(new_leader.name)
            set_rank(new_leader, CatRank.LEADER)
            clan.leader = new_leader
            clan.leader_lives = 9
            other_clan_event(
                f"{old_name} has received nine lives from StarClan and now leads "
                f"{clan.name} as {new_leader.name}.",
                new_leader,
            )
        else:
            clan.leader = None

    # DEPUTY
    if not still_holds(clan.deputy, clan, CatRank.DEPUTY):
        clan.deputy = None
        if warriors:
            clan.deputy = random.choice(warriors)
            warriors.remove(clan.deputy)
            set_rank(clan.deputy, CatRank.DEPUTY)
            other_clan_event(
                f"{clan.deputy.name} has been named the new deputy of {clan.name}.",
                clan.deputy,
            )

    # MEDICINE CAT
    if not still_holds(clan.medicine_cat, clan, CatRank.MEDICINE_CAT):
        clan.medicine_cat = None
        meds = [c for c in members if c.status.rank == CatRank.MEDICINE_CAT]
        if meds:
            clan.medicine_cat = meds[0]
        elif any(c.status.rank == CatRank.MEDICINE_APPRENTICE for c in members):
            pass  # an apprentice is already training, they'll take over when grown
        else:
            apprentices = [c for c in members if c.status.rank == CatRank.APPRENTICE]
            if apprentices:
                set_rank(random.choice(apprentices), CatRank.MEDICINE_APPRENTICE)
            elif warriors:
                clan.medicine_cat = random.choice(warriors)
                set_rank(clan.medicine_cat, CatRank.MEDICINE_CAT)


def handle_litters(clan, members):
    if len(members) >= get_config("multiclan.max_clan_size"):
        return

    chance = get_config("multiclan.litter_chance")
    if len(members) < get_config("multiclan.min_clan_size"):
        chance *= 3  # small Clans try harder to grow
    if random.random() > chance:
        return

    mothers = [
        c
        for c in members
        if c.gender == "female"
        and c.age in ADULT_AGES
        and c.status.rank != CatRank.MEDICINE_CAT
        and not c.birth_cooldown
        and not c.no_kits
    ]
    if not mothers:
        return
    mother = random.choice(mothers)

    # prefer the mother's mate if they're in the same Clan, otherwise a random tom
    fathers = [
        c for c in members if c.ID in mother.mate and c.gender == "male"
    ] or [
        c
        for c in members
        if c.gender == "male"
        and c.age in ADULT_AGES
        and not c.no_kits
        and not close_family(c, mother)
    ]
    father = random.choice(fathers) if fathers else None

    litter_range = get_config("multiclan.litter_size")
    kits = []
    for _ in range(random.randint(litter_range[0], litter_range[1])):
        kit = NewCatFactory.create_cat(
            moons=0,
            status_dict={"rank": CatRank.NEWBORN, "group_ID": clan.group_ID},
            parent1=mother.ID,
            parent2=father.ID if father else None,
            backstory="clanborn",
        )
        game.clan.add_cat(kit)
        kits.append(kit)

    for kit in kits:
        create_relationships_new_cat(kit)

    mother.birth_cooldown = get_config("multiclan.birth_cooldown")
    other_clan_event(
        f"{mother.name} of {clan.name} has given birth to a litter of "
        f"{len(kits)} kit{'s' if len(kits) > 1 else ''}.",
        mother,
    )