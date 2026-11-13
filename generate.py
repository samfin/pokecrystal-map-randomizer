#!/usr/bin/env python3
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maprando.parse import Disasm
from maprando.model import load_permissions
from maprando.catalog import Catalog, catalog_for
from maprando.gen import CaveGenerator, GenError

CAVES = ['UnionCave1F', 'UnionCaveB1F', 'UnionCaveB2F', 'DarkCaveVioletEntrance', 'DarkCaveBlackthornEntrance',
         'MountMortar1FInside', 'MountMortar2FInside', 'MountMortarB1F', 'RockTunnel1F', 'RockTunnelB1F',
         'MountMoon', 'DiglettsCave', 'SilverCaveRoom1', 'SilverCaveRoom2', 'SilverCaveRoom3', 'WhirlIslandNW',
         'WhirlIslandNE', 'WhirlIslandSW', 'WhirlIslandSE', 'WhirlIslandCave', 'WhirlIslandB1F', 'WhirlIslandB2F',
         'WhirlIslandLugiaChamber', 'SlowpokeWellB1F', 'SlowpokeWellB2F', 'TohjoFalls', 'DragonsDenB1F',
         'MountMortar1FOutside', 'SilverCaveItemRooms', 'DragonsDen1F',
         # towers
         'SproutTower1F', 'SproutTower2F', 'SproutTower3F',
         'BurnedTower1F', 'BurnedTowerB1F',
         'TinTower1F', 'TinTower2F', 'TinTower3F', 'TinTower4F', 'TinTower5F', 'TinTower6F',
         'TinTower7F', 'TinTower8F', 'TinTower9F', 'TinTowerRoof',
         'OlivineLighthouse1F', 'OlivineLighthouse2F', 'OlivineLighthouse3F',
         'OlivineLighthouse4F', 'OlivineLighthouse5F', 'OlivineLighthouse6F', 'IlexForest', 'VictoryRoad', 'IcePathB3F']
TOWERS = CAVES[CAVES.index('SproutTower1F'):]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--disasm', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pokecrystal-speedchoice2'))
    ap.add_argument('--maps', nargs='*', default=CAVES)
    ap.add_argument('--seed', default='1')
    ap.add_argument('--tries', type=int, default=200)
    a = ap.parse_args()
    d = Disasm(a.disasm)
    perms = load_permissions(d)
    cats = {}
    results = {}
    for n in a.maps:
        m = d.maps[n]
        cat = catalog_for(d, m, perms, cats)
        t0 = time.time()
        try:
            g = CaveGenerator(d, m, perms, cat)
            m2, k = g.generate(a.seed, a.tries)
            results[n] = m2
            print(f'OK   {n:28s} try {k:3d}  {time.time()-t0:5.1f}s')
        except GenError as e:
            print(f'FAIL {n:28s} {e}')
            print('      ', dict(g.errors.most_common(6)) if 'g' in dir() and hasattr(g, 'errors') else '')
    return d, results

if __name__ == '__main__':
    main()

ROUTES = ['Route1', 'Route2', 'Route3', 'Route4', 'Route5', 'Route6', 'Route7', 'Route8', 'Route9',
          'Route10North', 'Route10South', 'Route11', 'Route12', 'Route13', 'Route14', 'Route15', 'Route16',
          'Route17', 'Route18', 'Route19', 'Route20', 'Route21', 'Route22', 'Route24', 'Route25', 'Route26',
          'Route27', 'Route28', 'Route29', 'Route30', 'Route31', 'Route32', 'Route33', 'Route34', 'Route35',
          'Route36', 'Route37', 'Route38', 'Route39', 'Route40', 'Route41', 'Route42', 'Route43', 'Route44',
          'Route45', 'Route46']

# ice floors: rocks on the ice are redesigned by maprando/ice.py (B2F Mahogany side is a boulder
# drop puzzle shared with B1F's holes and stays vanilla)
ICE = ['IcePath1F', 'IcePathB1F', 'IcePathB2FBlackthornSide', 'IcePathB2FMahoganySide']

# other dungeons and outdoor areas (routes by another name, underground passages, the Rocket base)
OTHERS = ['Route23', 'SilverCaveOutside', 'LakeOfRage', 'MountMoonSquare', 'UndergroundPath', 'PowerPlant',
          'TeamRocketBaseB3F', 'GoldenrodUnderground', 'GoldenrodUndergroundWarehouse', 'FastShipB1F']
# not randomized: NationalPark/NationalParkBugContest (one shared layout, two sets of events),
# TeamRocketBaseB1F (nearly all locked by its traps), TeamRocketBaseB2F (laser barrier puzzle),
# GoldenrodUndergroundSwitchRoomEntrances (switch-operated doors: only the starting state is
# checked), RuinsOfAlphOutside/InnerChamber, RadioTower1F-5F, DragonShrine, CeladonMansionRoof
# (little or no room to change, or no layout found)

# gyms: drawn from scratch around the walk to the leader (maprando/gym.py says which and why)
from maprando.gym import ALL_GYMS as GYMS

ALL_MAPS = CAVES + ROUTES + ICE + OTHERS + GYMS

# maps the generator cannot rebuild yet: left vanilla without spending attempts on them
KNOWN_UNSUPPORTED = {
    'IcePathB2FMahoganySide': 'ice puzzle that depends on boulders dropped from B1F',
}
