"""
multiclan: monthly upkeep for the neighbouring Clans.

Keeps each Other Clan running on its own: replaces leaders, deputies and
medicine cats who die, leave or retire, and gives the Clans new litters
of kits so they don't dwindle away.
"""

import random

import i18n

from scripts.cat.cats import Cat
from scripts.cat.enums import CatAge, CatGroup, CatRank
from scripts.clan_package.settings import get_clan_setting
from scripts.cat.factories.new_cat_factory import NewCatFactory
from scripts.cat_relations.cat_handle_funcs import create_relationships_new_cat
from scripts.config import get_config
from scripts.events_module.event_information import EventInformation
from scripts.game_structure import constants, game
from scripts.game_structure.localization import load_lang_resource

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

        handle_natural_deaths(clan, members)

        # some cats may have died from their injuries or naturally
        members = get_members(clan)
        if not members:
            continue

        handle_joiners(clan, members)
        members = get_members(clan)

        handle_succession(clan, members)
        handle_mentors(clan, members)
        handle_relationships(clan, members)
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


def handle_joiners(clan, members):
    """
    Now and then a loner or rogue joins a neighbouring Clan. This brings in new
    blood: without it, small Clans become so interrelated that nobody can find
    a mate, and they slowly die out.
    """
    if len(members) >= get_config("multiclan.max_clan_size"):
        return
    chance = get_config("multiclan.joiner_chance")
    if len(members) < get_config("multiclan.min_clan_size"):
        chance *= 3
    if not any(
        c.gender == "female" and c.age in ADULT_AGES for c in members
    ) or not any(c.gender == "male" and c.age in ADULT_AGES for c in members):
        chance *= 3  # a Clan with no adult she-cats or toms badly needs new cats
    if random.random() > chance:
        return

    backstory = random.choice(["loner1", "rogue1"])
    new_cat = NewCatFactory.create_cat(
        moons=random.randint(14, 60),
        status_dict={"rank": CatRank.WARRIOR, "group_ID": clan.group_ID},
        backstory=backstory,
    )
    game.clan.add_cat(new_cat)
    create_relationships_new_cat(new_cat)
    other_clan_event(
        f"A {'loner' if backstory == 'loner1' else 'rogue'} has joined {clan.name}, "
        f"taking the name {new_cat.name}.",
        new_cat,
    )


def handle_natural_deaths(clan, members):
    """
    Neighbouring Clan cats die at the same rates as cats in your Clan:
    a small random chance each moon, old age past old_age_death_start,
    and leaders losing lives more often.
    """
    for cat in members:
        if cat.dead:
            continue
        cause = natural_death_cause(cat)
        if cause:
            natural_death(cat, clan, cause)


def natural_death_cause(cat):
    if cat.status.is_leader and not int(
        random.random() * get_config("death_related.leader_death_chance")
    ):
        return "leader"

    # same old age curve ClanGen uses for your Clan
    age_start = constants.CONFIG["death_related"]["old_age_death_start"]
    curve = 0.001 * constants.CONFIG["death_related"]["old_age_death_curve"]
    old_age_chance = ((1 + curve) ** (cat.moons - age_start)) - 1
    if random.random() <= old_age_chance or cat.moons >= 300:
        return "old_age"

    path = (
        "death_related.classic_death_chance"
        if game.clan.game_mode == "classic"
        else "death_related.death_chance"
    )
    if not int(random.random() * get_config(path)):
        return "random"
    return None


def natural_death(cat, clan, cause):
    if cause == "old_age":
        if clan.leader is cat:
            clan.leader_lives = 1  # old age takes all of a leader's lives
        cat.history.add_death(death_text="m_c died of old age.")
        text = (
            f"{cat.name} of {clan.name} passed away peacefully in their sleep "
            "after a long life."
        )
    else:
        deaths = load_lang_resource("events/death/outsider_deaths/outsider_deaths.json")
        text = random.choice(deaths["other_clan"]).replace("o_c_n", str(clan.name))
        history = i18n.t("events.death.outsider_deaths.history.other_clan")
        cat.history.add_death(death_text=history.replace("o_c_n", str(clan.name)))

    cat.die(grief_allowed=False)
    if cat.dead:
        game.cur_events_list.append(
            EventInformation(text, ["birth_death", "other_clans"], cat_dict={"m_c": cat})
        )


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
    Sorts this moon's neighbouring Clan events:
    - events between your cats and neighbouring cats go ONLY to the Other Clans tab
      (ClanGen's own events about your Clan, like your cats' pregnancies, births,
      mates and breakups, stay where ClanGen puts them)
    - events involving only neighbouring cats are removed (or, if
      show_neighbour_only_events is on, also listed under Other Clans only)
    """
    if not game.clan or game.clan.clancount != "multiclan":
        return

    neighbour_ids = {c.group_ID for c in game.clan.all_other_clans}
    show_neighbour_only = get_config("multiclan.show_neighbour_only_events")
    kept = []
    for event in game.cur_events_list:
        cats = [Cat.fetch_cat(cat_id) for cat_id in event.cats_involved]
        groups = {last_group(c) for c in cats if c}
        has_neighbour = bool(groups & neighbour_ids)
        has_player = CatGroup.PLAYER_CLAN_ID in groups

        if "other_clans" not in event.types:
            pass  # ClanGen's own events about your Clan (pregnancies, births, mates...) stay put
        elif has_neighbour and has_player:
            # "interaction" is what ClanGen uses to keep events off the main page
            event.types = ["other_clans", "interaction"]
        elif has_neighbour:
            if not show_neighbour_only:
                continue  # drop it
            event.types = ["other_clans", "interaction"]
        kept.append(event)
    game.cur_events_list[:] = kept


def last_group(cat):
    """The group a cat belongs (or, if dead, last belonged) to."""
    return cat.status.get_last_living_group() if cat.dead else cat.status.group_ID


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


def handle_mentors(clan, members):
    """
    Neighbouring Clan apprentices get mentors from their own Clan, learn from
    them over time, and become former apprentices once they graduate.
    Uses ClanGen's own mentor rules (warriors for apprentices, medicine cats
    for medicine apprentices, mediators for mediator apprentices).
    """
    for cat in members:
        old_mentor = cat.mentor

        if not cat.status.rank.is_any_apprentice_rank():
            if old_mentor:
                # they've graduated: ClanGen moves the mentor to former_mentor
                cat.update_mentor()
                cat.history.add_mentor_skill_influence_strings()
                cat.history.add_mentor_facet_influence_strings()
            continue

        # drop a mentor who died, left, or changed jobs, then find a new one
        cat.update_mentor()
        if not cat.mentor:
            cat.assign_random_mentor()

        mentor = Cat.fetch_cat(cat.mentor) if cat.mentor else None
        if not mentor:
            continue

        if cat.mentor != old_mentor:
            other_clan_event(
                f"{mentor.name} of {clan.name} has been chosen as {cat.name}'s mentor.",
                cat,
            )
        elif random.random() < get_config("multiclan.mentor_influence_chance"):
            # training together shapes the apprentice's personality and skills
            facet = cat.personality.mentor_influence(mentor.personality)
            skill = cat.skills.mentor_influence(mentor)
            if facet:
                cat.history.add_facet_mentor_influence(mentor.ID, facet[0], facet[1])
            if skill:
                cat.history.add_skill_mentor_influence(skill[0], skill[1], skill[2])


def handle_relationships(clan, members):
    """
    Neighbouring Clan cats interact with their Clanmates each moon, so their
    friendships, rivalries and crushes grow and change like your Clan's do.
    Uses ClanGen's own relationship interactions.
    """
    # imported here to avoid a circular import
    from scripts.events_module.relationship import relation_events
    from scripts.cat_relations.enums import RelType

    first_new_event = len(game.cur_events_list)
    chance = get_config("multiclan.neighbour_interaction_chance")
    active = [c for c in members if c.status.rank != CatRank.NEWBORN]

    for cat in active:
        if random.random() > chance:
            continue
        clanmates = [c for c in active if c is not cat]
        if not clanmates:
            continue

        # everyday interaction with a random Clanmate
        relation_events._trigger_pair_event(  # pylint: disable=protected-access
            cat, random.choice(clanmates)
        )

        # sometimes, a romantic moment with their mate or a cat they get along with
        if cat.moons >= 12 and not random.getrandbits(3):
            crushes = [
                c
                for c in clanmates
                if c.ID in cat.mate
                or (
                    not cat.no_mates
                    and cat.is_potential_mate(c, for_love_interest=True)
                    and get_like(cat, c) > 10
                    and get_like(c, cat) > 10
                )
            ]
            if crushes:
                relation_events._trigger_pair_event(  # pylint: disable=protected-access
                    cat, random.choice(crushes), RelType.ROMANCE
                )

    # move the interaction events to the Other Clans tab, or drop them
    new_events = game.cur_events_list[first_new_event:]
    del game.cur_events_list[first_new_event:]
    if get_config("multiclan.show_neighbour_interactions"):
        for event in new_events:
            event.types = ["other_clans", "interaction"]
        game.cur_events_list.extend(new_events)


def get_like(cat, other) -> int:
    rel = cat.relationships.get(other.ID)
    return max(rel.like, rel.comfort) if rel else 0


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

    # with "Pregnancy ignores biology" on, any cat can carry kits
    any_gender = get_clan_setting("same sex birth")
    mothers = [
        c
        for c in members
        if (any_gender or c.gender == "female")
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
    any_gender = get_clan_setting("same sex birth")
    mates = [
        m
        for m in living_mates(mother)
        if (any_gender or m.gender != mother.gender)
        and m.status.group_ID == clan.group_ID
        and not m.no_kits
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
            or (other.gender == cat.gender and not get_clan_setting("same sex birth"))
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
    Called from ClanGen's pregnancy code when one of YOUR cats has kits with a
    neighbouring Clan cat. If the neighbour is the one who would carry the kits,
    they're born into the neighbour's Clan instead. Returns True if handled here.
    """
    clan = neighbour_clan_of(pregnant_cat)
    if not clan:
        return False
    create_litter(clan, pregnant_cat, second_parent, half_clan=True)
    return True