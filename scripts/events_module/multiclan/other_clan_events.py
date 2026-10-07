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
            handle_conditions(cat, clan)

        # some cats may have died from their injuries
        members = get_members(clan)
        if not members:
            continue

        handle_succession(clan, members)
        handle_litters(clan, members)


def handle_conditions(cat, clan):
    for conditions in (cat.injuries, cat.illnesses):
        for name, info in list(conditions.items()):
            if cat.dead:
                return
            if name == "pregnant":
                continue

            # conditions don't do anything the moon they're gained
            if info.get("event_triggered"):
                info["event_triggered"] = False
                continue

            mortality = info.get("mortality", 0)
            # leaders have a higher chance of death, same as in your Clan
            if cat.status.is_leader and mortality:
                mortality = max(1, int(mortality * 0.7))

            if mortality and not int(random.random() * mortality):
                condition_death(cat, clan, name)
                continue

            if game.clan.age - info.get("moon_start", 0) >= info.get("duration", 0):
                del conditions[name]


def condition_death(cat, clan, condition_name):
    """A neighbouring Clan cat dies (or their leader loses a life) from a condition."""
    was_leader = cat.status.is_leader
    cat.history.add_death(
        death_text=f"m_c died from {condition_name}."
    )
    cat.die()
    if cat.dead:
        game.cur_events_list.append(
            EventInformation(
                f"{'The leader of ' + str(clan.name) + ', ' if was_leader else ''}"
                f"{cat.name} of {clan.name} has died from {condition_name}.",
                ["birth_death", "other_clans"],
                cat_dict={"m_c": cat},
            )
        )


def other_clan_leader_loses_life(cat) -> bool:
    """
    Called when a neighbouring Clan's leader dies. If they have lives to spare,
    they lose one and come back. Returns True if the leader survived.
    """
    clan = next(
        (c for c in game.clan.all_other_clans if c.group_ID == cat.status.group_ID),
        None,
    )
    if not clan or clan.leader is not cat:
        return False
    if clan.leader_lives <= 1:
        clan.leader_lives = 0
        return False

    clan.leader_lives -= 1
    cat.injuries.clear()
    cat.illnesses.clear()
    lives = clan.leader_lives
    other_clan_event(
        f"{cat.name} lost a life, but StarClan sent them back to lead {clan.name}. "
        f"{lives} {'life remains' if lives == 1 else 'lives remain'}.",
        cat,
    )
    return True


def hide_neighbour_events():
    """
    Events that only involve neighbouring Clan cats are moved off the main
    events page; they still show under Other Clans.
    """
    if not game.clan or game.clan.clancount != "multiclan":
        return
    if get_config("multiclan.show_neighbour_events_in_all"):
        return

    neighbour_ids = {c.group_ID for c in game.clan.all_other_clans}
    for event in game.cur_events_list:
        if "other_clans" not in event.types or "interaction" in event.types:
            continue
        cats = [Cat.fetch_cat(cat_id) for cat_id in event.cats_involved]
        cats = [c for c in cats if c]
        if cats and all(
            (c.status.get_last_living_group() if c.dead else c.status.group_ID)
            in neighbour_ids
            for c in cats
        ):
            # "interaction" is what ClanGen uses to keep minor events off the main page
            event.types.append("interaction")


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