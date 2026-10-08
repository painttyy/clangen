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

    mark_halfclan_kits()

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
        handle_mates(clan, members)
        handle_litters(clan, members)


def mark_halfclan_kits():
    """
    Kits just born in YOUR Clan with a parent from a neighbouring Clan get the
    half-Clan backstory (ClanGen's own birth code doesn't know about neighbours).
    """
    neighbour_ids = {c.group_ID for c in game.clan.all_other_clans}
    for kit in Cat.all_cats_list:
        if kit.moons != 0 or kit.dead or not kit.status.alive_in_player_clan:
            continue
        for parent_id in (kit.parent1, kit.parent2):
            parent = Cat.fetch_cat(parent_id) if parent_id else None
            if parent and (
                parent.status.get_last_living_group() if parent.dead else parent.status.group_ID
            ) in neighbour_ids:
                kit.backstory = "halfclan1"
                break


def handle_conditions(cat, clan):
    """
    Official ClanGen doesn't run injuries and illnesses for cats outside your Clan,
    so this does a simpler version for neighbouring Clan cats: each moon a condition
    can kill them (using the same mortality as your Clan's cats), otherwise they
    recover once it has run its course.

    Cat.moon_skip_injury() isn't used because it would take lives from YOUR leader.
    """
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


def handle_mates(clan, members):
    """Neighbouring Clan cats pair up as mates, and widowed cats eventually move on."""
    # moving on: a cat whose mates have all died may unset them after a while
    for cat in members:
        if not cat.mate:
            continue
        mates = [Cat.fetch_cat(m) for m in cat.mate]
        if all(m is None or m.dead for m in mates) and random.random() < 1 / 12:
            for mate in mates:
                if mate:
                    cat.unset_mate(mate)

    if random.random() > get_config("multiclan.mate_chance"):
        return

    singles = [
        c
        for c in members
        if c.age.can_have_mate()
        and c.moons >= 14
        and not c.no_mates
        and not living_mates(c)
    ]
    random.shuffle(singles)
    for cat in singles:
        partners = [
            other
            for other in singles
            if other is not cat and cat.is_potential_mate(other)
        ]
        if not partners:
            continue
        # cats who already have feelings for each other are more likely to pair up
        weights = [max(1, romance(cat, other) + 10) for other in partners]
        partner = random.choices(partners, weights)[0]
        cat.set_mate(partner)
        other_clan_event(
            f"{cat.name} and {partner.name} of {clan.name} have become mates.",
            cat,
        )
        return


def living_mates(cat) -> list:
    mates = [Cat.fetch_cat(m) for m in cat.mate]
    return [m for m in mates if m and not m.dead]


def romance(cat, other) -> int:
    rel = cat.relationships.get(other.ID)
    return rel.romance if rel else 0


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
    random.shuffle(mothers)
    for mother in mothers:
        father, half_clan = choose_father(mother, clan)
        if father:
            create_litter(clan, mother, father, half_clan)
            return


def choose_father(mother, clan):
    """
    Returns (father, is_half_clan). Mothers have kits with their mate, or, if
    half-Clan kits are on, sometimes with a cat from another Clan they love.
    """
    mates = [
        m
        for m in living_mates(mother)
        if m.gender == "male" and m.status.group_ID == clan.group_ID and not m.no_kits
    ]
    love = halfclan_love(mother)
    if love and (not mates or random.random() < get_config("multiclan.halfclan_affair_chance")):
        return love, True
    if mates:
        return random.choice(mates), False
    return None, False


def halfclan_love(cat):
    """The cat from another Clan this cat loves most, if the love is strong enough."""
    if not get_config("multiclan.halfclan_kits"):
        return None
    best, best_romance = None, get_config("multiclan.halfclan_min_romance") - 1
    for other_id, rel in cat.relationships.items():
        other = Cat.fetch_cat(other_id)
        if (
            not other
            or other.dead
            or other.gender == cat.gender
            or other.no_kits
            or other.status.group_ID == cat.status.group_ID
            or not (other.status.alive_in_player_clan or other.status.is_other_clancat)
            or not cat.is_potential_mate(other, for_love_interest=True)
        ):
            continue
        if rel.romance > best_romance:
            best, best_romance = other, rel.romance
    return best


def neighbour_clan_of(cat):
    return next(
        (c for c in game.clan.all_other_clans if c.group_ID == cat.status.group_ID),
        None,
    )


def create_litter(clan, mother, father, half_clan=False):
    """Creates a litter of kits in the mother's (neighbouring) Clan."""
    litter_range = get_config("multiclan.litter_size")
    kits = []
    for _ in range(random.randint(litter_range[0], litter_range[1])):
        kit = NewCatFactory.create_cat(
            moons=0,
            status_dict={"rank": CatRank.NEWBORN, "group_ID": clan.group_ID},
            parent1=mother.ID,
            parent2=father.ID if father else None,
            backstory="halfclan1" if half_clan else "clanborn",
        )
        game.clan.add_cat(kit)
        kits.append(kit)

    for kit in kits:
        create_relationships_new_cat(kit)

    mother.birth_cooldown = get_config("multiclan.birth_cooldown")
    count = f"{len(kits)} kit{'s' if len(kits) > 1 else ''}"
    if half_clan:
        father_clan = (
            game.clan if father.status.alive_in_player_clan else neighbour_clan_of(father)
        )
        text = (
            f"{mother.name} of {clan.name} has given birth to a litter of {count}. "
            f"Their father is {father.name} of {father_clan.name if father_clan else 'another Clan'}, "
            f"making them half-Clan."
        )
        game.cur_events_list.append(
            EventInformation(
                text,
                ["birth_death", "other_clans"],
                cat_dict={"m_c": mother, "r_c": father},
            )
        )
    else:
        other_clan_event(
            f"{mother.name} of {clan.name} has given birth to a litter of {count}.",
            mother,
        )
    return kits


def player_halfclan_pregnancy(pregnant_cat, second_parent) -> bool:
    """
    When one of your cats has kits with a
    neighbouring Clan cat. If the neighbour is the one who would carry the kits,
    they're born into the neighbour's Clan instead. Returns True if handled here.
    """
    clan = neighbour_clan_of(pregnant_cat)
    if not clan:
        return False
    create_litter(clan, pregnant_cat, second_parent, half_clan=True)
    return True