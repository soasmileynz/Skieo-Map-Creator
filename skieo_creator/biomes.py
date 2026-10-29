"""The game's 17 biomes: automatic choice from the location, default treeline / snowfall and the tree mix.

The automatic biome is the biome of the nearest real resort in the game's own map list (DA_LevelRegistry: every entry
has a latitude, longitude and biome), so a window in Chile gets the Andes, one in Norway the Scandes, and so on.
Trees: each planted tree takes its size / health / shape from a stock forest at the same relative elevation (Sugar
Bowl: aspen, fir, pine, spruce, whitebark pine; Mad River Glen: birch, fir, maple, oak, spruce) and its species is then
mapped onto the biome's own species list (DA_BiomeSettings_*), with the meshes the game itself uses for that species.
"""
from . import geo

# (name, latitude, longitude, biome) of the game's own map entries (build 26174965)
STOCK = [
    ('Whistler Blackcomb', 50.087, -122.921, 'CoastMountains'), ('Lake Louise', 51.442, -116.165, 'CanadianRockies'),
    ('Sunshine Village', 51.078, -115.776, 'CanadianRockies'), ('Kicking Horse', 51.298, -117.048, 'CanadianRockies'),
    ('Are', 63.400, 13.035, 'Scandes'), ('Hemsedal', 60.876, 8.530, 'Scandes'), ('Trysil', 61.308, 12.172, 'Scandes'),
    ('Niseko', 42.862, 140.678, 'Hokkaido'), ('Rusutsu', 42.742, 140.908, 'Hokkaido'), ('Furano', 43.314, 142.360, 'Hokkaido'),
    ('Yongpyong', 37.644, 128.681, 'Taebaek'), ('High1', 37.209, 128.830, 'Taebaek'), ('Muju', 35.885, 127.740, 'Taebaek'),
    ('Changbaishan', 42.102, 127.494, 'Changbai'), ('Beidahu', 43.412, 126.655, 'Changbai'),
    ('Songhua Lake', 43.673, 126.618, 'Changbai'), ('Thredbo', -36.503, 148.305, 'AustralianAlps'),
    ('Perisher', -36.394, 148.397, 'AustralianAlps'), ('Mt Buller', -37.146, 146.436, 'AustralianAlps'),
    ('The Remarkables', -45.055, 168.820, 'SouthernAlpsNZ'), ('Cardrona', -44.874, 168.948, 'SouthernAlpsNZ'),
    ('Coronet Peak', -44.920, 168.733, 'SouthernAlpsNZ'), ('Valle Nevado', -33.342, -70.273, 'Andes'),
    ('Portillo', -32.835, -70.128, 'Andes'), ('Cerro Catedral', -41.167, -71.442, 'Patagonia'),
    ('Mauna Kea', 19.821, -155.468, 'HawaiiAlpine'), ('Zermatt', 46.005, 7.735, 'Alps'), ('Livigno', 46.525, 10.120, 'Alps'),
    ('Murren', 46.558, 7.864, 'Alps'), ('Chamonix', 45.945, 6.904, 'Alps'), ('Soda Springs', 39.320, -120.380, 'Tahoe'),
    ('Sugar Bowl', 39.300, -120.330, 'Tahoe'), ('Palisades Tahoe', 39.200, -120.240, 'Tahoe'),
    ('Sunlight', 39.400, -107.340, 'Rockies'), ('Steamboat', 40.460, -106.810, 'Rockies'), ('Vail', 39.620, -106.380, 'Rockies'),
    ('Brighton', 40.600, -111.580, 'Utah'), ('Snowbird', 40.580, -111.660, 'Utah'), ('Park City', 40.650, -111.510, 'Utah'),
    ('Mt Baker', 48.860, -121.680, 'PNW'), ('Crystal Mountain', 46.930, -121.470, 'PNW'), ('Mt Bachelor', 43.980, -121.690, 'PNW'),
    ('Mad River Glen', 44.200, -72.920, 'Northeast'), ('Stowe', 44.530, -72.780, 'Northeast'),
    ('Killington', 43.600, -72.820, 'Northeast'),
]

# donor forest ('sb' Sugar Bowl / 'mrg' Mad River Glen), species map donor -> biome species, default snowfall (median
# of the biome's own entries), treeline (m) as a + b * (|lat| - lat0), and extra rules
BIOMES = {
    'Northeast': dict(label='Northeast (New England / Quebec)', donor='mrg', map={}, snow=1.0,
                      treeline=(1250.0, -45.0, 44.0)),
    'Rockies': dict(label='Rocky Mountains', donor='sb', map={}, snow=0.96, treeline=(3550.0, -110.0, 39.0)),
    'CanadianRockies': dict(label='Canadian Rockies', donor='sb', map={}, snow=1.6, treeline=(2250.0, -60.0, 51.0),
                            larch=0.18),
    'Tahoe': dict(label='Sierra Nevada / Tahoe', donor='sb', map={}, snow=1.05, treeline=(3000.0, -150.0, 38.5)),
    'Utah': dict(label='Utah (Wasatch)', donor='sb', map={'Pine': 'Fir', 'WhitebarkPine': 'Spruce'}, snow=1.1,
                 treeline=(3300.0, -100.0, 40.6), oak=(2450.0, 450.0, 0.55)),
    'PNW': dict(label='Pacific Northwest (Cascades)', donor='mrg', map={'Oak': 'Hemlock', 'Birch': 'Hemlock'}, snow=1.0,
                treeline=(2400.0, -170.0, 44.0)),
    'CoastMountains': dict(label='Coast Mountains (BC)', donor='mrg', map={'Oak': 'Hemlock', 'Birch': 'Hemlock'},
                           snow=1.0, treeline=(1900.0, -60.0, 50.0)),
    'Alps': dict(label='European Alps', donor='sb', map={'Fir': 'Spruce', 'WhitebarkPine': 'Pine', 'Aspen': 'Birch'},
                 snow=0.95, treeline=(2200.0, -40.0, 46.0), larch=0.2),
    'Scandes': dict(label='Scandinavia', donor='sb', map={'Fir': 'Spruce', 'WhitebarkPine': 'Pine', 'Aspen': 'MountainBirch'},
                    snow=0.75, treeline=(1000.0, -55.0, 61.0)),
    'Hokkaido': dict(label='Japan (Hokkaido / Honshu)', donor='mrg', map={'Maple': 'MountainBirch'}, snow=0.8,
                     treeline=(1100.0, -100.0, 43.0)),
    'Changbai': dict(label='Northeast China', donor='sb', map={'Fir': 'Larch', 'WhitebarkPine': 'Pine', 'Aspen': 'Birch'},
                     snow=0.9, treeline=(2000.0, -60.0, 42.0)),
    'Taebaek': dict(label='Korea', donor='mrg', map={'Fir': 'Pine', 'Spruce': 'Pine', 'Maple': 'Oak'}, snow=0.9,
                    treeline=(2000.0, 0.0, 37.0)),
    'Andes': dict(label='Andes (Chile / Argentina)', donor='sb',
                  map={'Aspen': 'Sclerophyll', 'Pine': 'Sclerophyll', 'WhitebarkPine': 'Sclerophyll', 'Spruce': 'Fir'},
                  snow=1.0, treeline=(2400.0, -60.0, 33.0)),
    'Patagonia': dict(label='Patagonia', donor='mrg', map='SouthernBeech', snow=1.0, treeline=(1650.0, -45.0, 41.0)),
    'SouthernAlpsNZ': dict(label='New Zealand', donor='mrg', map='SouthernBeech', snow=0.8, treeline=(1300.0, -30.0, 43.0)),
    'AustralianAlps': dict(label='Australian Alps', donor='sb', map='SnowGum', snow=0.94, treeline=(1850.0, -60.0, 36.5)),
    'HawaiiAlpine': dict(label='Hawaii (alpine)', donor=None, map={}, snow=1.0, treeline=(0.0, 0.0, 20.0)),
}


# AverageAnnualSnowfallInches of each DA_BiomeSettings_*: the game's reference snowfall for the biome, which the page
# scales by the snowfall multiplier for a rough per-season estimate
SNOWFALL_IN = {
    'Alps': 280, 'Andes': 288, 'AustralianAlps': 80, 'CanadianRockies': 180, 'Changbai': 70, 'CoastMountains': 460,
    'HawaiiAlpine': 40, 'Hokkaido': 590, 'Northeast': 182, 'Patagonia': 118, 'PNW': 388, 'Rockies': 274, 'Scandes': 160,
    'SouthernAlpsNZ': 150, 'Taebaek': 100, 'Tahoe': 275, 'Utah': 350,
}


def nearest(lat, lon):
    """(stock resort name, biome, distance km) closest to the point"""
    best = min(STOCK, key=lambda s: geo.haversine_km(lat, lon, s[1], s[2]))
    return best[0], best[3], geo.haversine_km(lat, lon, best[1], best[2])


def default_treeline(biome, lat):
    a, b, lat0 = BIOMES[biome]['treeline']
    return max(0.0, a + b * (abs(lat) - lat0))


def defaults(lat, lon):
    name, biome, km = nearest(lat, lon)
    return dict(biome=biome, near=name, near_km=round(km), treeline=round(default_treeline(biome, lat)),
                snow=BIOMES[biome]['snow'], treelines={b: round(default_treeline(b, lat)) for b in BIOMES},
                snows={b: v['snow'] for b, v in BIOMES.items()})


def listing():
    return [dict(id=k, label=v['label'], snow=v['snow'], inches=SNOWFALL_IN.get(k)) for k, v in sorted(BIOMES.items(), key=lambda kv: kv[1]['label'])]
