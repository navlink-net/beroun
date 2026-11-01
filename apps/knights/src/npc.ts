import {
  ArmorType, HorseType, WeaponType, MedicineType,
  DressType, AccessoryType, PotionType, SpellType,
  Equipment, LadyOutfit,
  KnightPlayer, LadyPlayer,
  NpcDifficulty,
  KnightTitle, LadyTitle,
  ACCESSORY_STATS, POTION_STATS,
  computeKnightStats,
} from './shared';

// ─── Names ───────────────────────────────────────────────────────────────────

export const KNIGHT_NAMES = [
  'Sir William',   'Sir Hugh',       'Sir Geoffrey',  'Sir Ranulf',
  'Sir Baldwyn',   'Sir Osbert',     'Sir Reginald',  'Sir Eustace',
  'Sir Guillaume', 'Sir Gautier',    'Sir Bertrand',  'Sir Renaud',
  'Sir Thibaut',   'Sir Girard',     'Sir Raoul',     'Sir Godefroy',
  'Sir Arnaud',    'Sir Joscelin',   'Sir Anselm',    'Sir Humphrey',
  'Sir Heinrich',  'Sir Konrad',     'Sir Friedrich', 'Sir Gottfried',
  'Sir Ludwig',    'Sir Dietrich',   'Sir Burkhard',  'Sir Hartmann',
  'Sir Wolfram',   'Sir Ruprecht',   'Sir Albrecht',  'Sir Wigand',
];

export const LADY_NAMES = [
  'Lady Matilda',    'Lady Eleanor',    'Lady Cecily',     'Lady Maud',
  'Lady Avice',      'Lady Beatrice',   'Lady Petronilla', 'Lady Constance',
  'Lady Margery',    'Lady Hawise',     'Lady Alienor',    'Lady Blanche',
  'Lady Marguerite', 'Lady Isabeau',    'Lady Alix',       'Lady Heloise',
  'Lady Ermengarde', 'Lady Adele',      'Lady Clemence',   'Lady Philippa',
  'Lady Hildegard',  'Lady Mechthild',  'Lady Agnes',      'Lady Sophia',
  'Lady Elisabeth',  'Lady Kunigunde',  'Lady Hedwig',     'Lady Gertrude',
  'Lady Richenza',   'Lady Gisela',     'Lady Adelheid',   'Lady Sybilla',
];

// ─── Static profiles (equipment per NPC slot) ─────────────────────────────

const KNIGHT_PROFILES: Equipment[] = [
  { armor: ArmorType.LEATHER,   horse: HorseType.PALFREY,  weapon: WeaponType.SWORD,  medicine: MedicineType.HERBS   },
  { armor: ArmorType.LEATHER,   horse: HorseType.PALFREY,  weapon: WeaponType.LANCE,  medicine: MedicineType.HERBS   },
  { armor: ArmorType.LEATHER,   horse: HorseType.COURSER,  weapon: WeaponType.SWORD,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.LEATHER,   horse: HorseType.COURSER,  weapon: WeaponType.AXE,    medicine: MedicineType.HERBS   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.PALFREY,  weapon: WeaponType.SWORD,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.COURSER,  weapon: WeaponType.LANCE,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.COURSER,  weapon: WeaponType.MACE,   medicine: MedicineType.HERBS   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.COURSER,  weapon: WeaponType.AXE,    medicine: MedicineType.TONIC   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.DESTRIER, weapon: WeaponType.SWORD,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.DESTRIER, weapon: WeaponType.LANCE,  medicine: MedicineType.POTION  },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.PALFREY,  weapon: WeaponType.MACE,   medicine: MedicineType.POTION  },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.ROUNCY,   weapon: WeaponType.LANCE,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.PLATE,     horse: HorseType.COURSER,  weapon: WeaponType.SWORD,  medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.COURSER,  weapon: WeaponType.LANCE,  medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.COURSER,  weapon: WeaponType.MACE,   medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.DESTRIER, weapon: WeaponType.SWORD,  medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.DESTRIER, weapon: WeaponType.LANCE,  medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.PLATE,     horse: HorseType.DESTRIER, weapon: WeaponType.MACE,   medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.ROUNCY,   weapon: WeaponType.AXE,    medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.PALFREY,  weapon: WeaponType.SWORD,  medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.ROUNCY,   weapon: WeaponType.MACE,   medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.ROUNCY,   weapon: WeaponType.AXE,    medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.DESTRIER, weapon: WeaponType.LANCE,  medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.DESTRIER, weapon: WeaponType.MACE,   medicine: MedicineType.POTION  },
  { armor: ArmorType.LEATHER,   horse: HorseType.DESTRIER, weapon: WeaponType.LANCE,  medicine: MedicineType.POTION  },
  { armor: ArmorType.LEATHER,   horse: HorseType.ROUNCY,   weapon: WeaponType.MACE,   medicine: MedicineType.TONIC   },
  { armor: ArmorType.PLATE,     horse: HorseType.PALFREY,  weapon: WeaponType.AXE,    medicine: MedicineType.TONIC   },
  { armor: ArmorType.CHAINMAIL, horse: HorseType.ROUNCY,   weapon: WeaponType.AXE,    medicine: MedicineType.POTION  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.COURSER,  weapon: WeaponType.SWORD,  medicine: MedicineType.TONIC   },
  { armor: ArmorType.LEATHER,   horse: HorseType.ROUNCY,   weapon: WeaponType.AXE,    medicine: MedicineType.ELIXIR  },
  { armor: ArmorType.FULLPLATE, horse: HorseType.PALFREY,  weapon: WeaponType.AXE,    medicine: MedicineType.POTION  },
  { armor: ArmorType.PLATE,     horse: HorseType.ROUNCY,   weapon: WeaponType.SWORD,  medicine: MedicineType.ELIXIR  },
];

const LADY_PROFILES: LadyOutfit[] = [
  { dress: DressType.NOBLE,     accessory: AccessoryType.WAND,   potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.WAND,   potion: PotionType.AMPLIFIER, medicine: MedicineType.TONIC   },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.WAND,   potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS   },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.WAND,   potion: PotionType.CLARITY,   medicine: MedicineType.POTION  },
  { dress: DressType.NOBLE,     accessory: AccessoryType.AMULET, potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.AMULET, potion: PotionType.MANA_BREW, medicine: MedicineType.TONIC   },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.AMULET, potion: PotionType.MANA_BREW, medicine: MedicineType.POTION  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.AMULET, potion: PotionType.CLARITY,   medicine: MedicineType.HERBS   },
  { dress: DressType.NOBLE,     accessory: AccessoryType.AMULET, potion: PotionType.AMPLIFIER, medicine: MedicineType.TONIC   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.AMULET, potion: PotionType.CLARITY,   medicine: MedicineType.ELIXIR  },
  { dress: DressType.NOBLE,     accessory: AccessoryType.MIRROR, potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.MIRROR, potion: PotionType.VENOM,     medicine: MedicineType.TONIC   },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.MIRROR, potion: PotionType.MANA_BREW, medicine: MedicineType.POTION  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.MIRROR, potion: PotionType.CLARITY,   medicine: MedicineType.HERBS   },
  { dress: DressType.NOBLE,     accessory: AccessoryType.MIRROR, potion: PotionType.VENOM,     medicine: MedicineType.ELIXIR  },
  { dress: DressType.ROYAL,     accessory: AccessoryType.MIRROR, potion: PotionType.AMPLIFIER, medicine: MedicineType.POTION  },
  { dress: DressType.NOBLE,     accessory: AccessoryType.CROWN,  potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.CROWN,  potion: PotionType.AMPLIFIER, medicine: MedicineType.TONIC   },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.CROWN,  potion: PotionType.MANA_BREW, medicine: MedicineType.POTION  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.CROWN,  potion: PotionType.CLARITY,   medicine: MedicineType.HERBS   },
  { dress: DressType.NOBLE,     accessory: AccessoryType.CROWN,  potion: PotionType.VENOM,     medicine: MedicineType.TONIC   },
  { dress: DressType.ROYAL,     accessory: AccessoryType.CROWN,  potion: PotionType.AMPLIFIER, medicine: MedicineType.ELIXIR  },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.CROWN,  potion: PotionType.CLARITY,   medicine: MedicineType.POTION  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.AMULET, potion: PotionType.AMPLIFIER, medicine: MedicineType.TONIC   },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.WAND,   potion: PotionType.VENOM,     medicine: MedicineType.POTION  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.MIRROR, potion: PotionType.MANA_BREW, medicine: MedicineType.ELIXIR  },
  { dress: DressType.ROYAL,     accessory: AccessoryType.WAND,   potion: PotionType.VENOM,     medicine: MedicineType.POTION  },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.AMULET, potion: PotionType.CLARITY,   medicine: MedicineType.ELIXIR  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.CROWN,  potion: PotionType.VENOM,     medicine: MedicineType.POTION  },
  { dress: DressType.ROYAL,     accessory: AccessoryType.MIRROR, potion: PotionType.CLARITY,   medicine: MedicineType.ELIXIR  },
  { dress: DressType.MYSTIC,    accessory: AccessoryType.CROWN,  potion: PotionType.VENOM,     medicine: MedicineType.ELIXIR  },
  { dress: DressType.ENCHANTED, accessory: AccessoryType.WAND,   potion: PotionType.AMPLIFIER, medicine: MedicineType.POTION  },
];

// ─── Pre-computed roster (design time — runs once at module load) ─────────

export type KnightTemplate = { name: string; equipment: Equipment; baseStats: ReturnType<typeof computeKnightStats>; avatarUrl: string };
export type LadyTemplate   = { name: string; outfit: LadyOutfit; magicPower: number; maxMagicPower: number; avatarUrl: string };

export const NPC_KNIGHT_ROSTER: readonly KnightTemplate[] = KNIGHT_PROFILES.map((equipment, i) => ({
  name: KNIGHT_NAMES[i],
  equipment,
  baseStats: computeKnightStats(equipment),
  avatarUrl: `/assets/avatar/k${String(i + 1).padStart(2, '0')}.png`,
}));

export const NPC_LADY_ROSTER: readonly LadyTemplate[] = LADY_PROFILES.map((outfit, i) => {
  let mp = 100;
  if (outfit.dress === DressType.MYSTIC) mp += 20;
  mp += POTION_STATS[outfit.potion].mpBonus;
  return {
    name: LADY_NAMES[i], outfit, magicPower: mp, maxMagicPower: mp,
    avatarUrl: `/assets/avatar/l${String(i + 1).padStart(2, '0')}.png`,
  };
});

// ─── Random generation (used for fillNpcs at tournament start if needed) ──

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

let npcSeq = 0;
function npcId(): string { return `npc_${++npcSeq}`; }

export function generateNpcKnight(nameHint?: string, difficulty: NpcDifficulty = NpcDifficulty.SQUIRE): KnightPlayer {
  let eq: Equipment;
  if (difficulty === NpcDifficulty.CHAMPION) {
    eq = { armor: ArmorType.PLATE, horse: HorseType.DESTRIER, weapon: WeaponType.LANCE, medicine: MedicineType.ELIXIR };
  } else if (difficulty === NpcDifficulty.KNIGHT) {
    eq = {
      armor:    pick([ArmorType.CHAINMAIL, ArmorType.PLATE]),
      horse:    pick([HorseType.DESTRIER, HorseType.COURSER]),
      weapon:   pick([WeaponType.LANCE, WeaponType.MACE, WeaponType.AXE]),
      medicine: pick([MedicineType.TONIC, MedicineType.POTION]),
    };
  } else {
    eq = {
      armor:    pick(Object.values(ArmorType)),
      horse:    pick([HorseType.ROUNCY, HorseType.DESTRIER, HorseType.COURSER, HorseType.PALFREY]),
      weapon:   pick(Object.values(WeaponType)),
      medicine: pick(Object.values(MedicineType)),
    };
  }
  return {
    id: npcId(), name: nameHint ?? pick(KNIGHT_NAMES),
    isNPC: true, npcDifficulty: difficulty, role: 'knight',
    points: 0, isConnected: true, lobbyReady: true,
    equipment: eq, baseStats: computeKnightStats(eq),
    chosenLadies: [null, null, null], title: KnightTitle.SQUIRE, prepareReady: true,
    healthWorn: 1, armorWornPct: 1, weaponWornPct: 1,
    capturedArmors: [], capturedHorses: [],
  };
}

export function generateNpcLady(nameHint?: string, difficulty: NpcDifficulty = NpcDifficulty.SQUIRE): LadyPlayer {
  const outfit: LadyOutfit = {
    dress:     pick(Object.values(DressType)),
    accessory: pick(Object.values(AccessoryType)),
    potion:    pick(Object.values(PotionType)),
    medicine:  pick(Object.values(MedicineType)),
  };
  let mp = 100;
  if (outfit.dress === DressType.MYSTIC) mp += 20;
  mp += POTION_STATS[outfit.potion].mpBonus;
  return {
    id: npcId(), name: nameHint ?? pick(LADY_NAMES),
    isNPC: true, npcDifficulty: difficulty, role: 'lady',
    points: 0, isConnected: true, lobbyReady: true,
    outfit, magicPower: mp, maxMagicPower: mp,
    chosenKnights: [null, null, null], title: LadyTitle.MAIDEN, prepareReady: true,
    status: 0,
  };
}

export function npcChoosePartners<T extends { id: string }>(
  pool: T[], _difficulty: NpcDifficulty,
): [string | null, string | null, string | null] {
  const shuffled = [...pool].sort(() => Math.random() - 0.5).slice(0, 3);
  return [shuffled[0]?.id ?? null, shuffled[1]?.id ?? null, shuffled[2]?.id ?? null];
}

export function npcDecideSpell(
  lady: LadyPlayer, ownKnightHpRatio: number, enemyKnightHpRatio: number, difficulty: NpcDifficulty,
): SpellType | null {
  const spells = ACCESSORY_STATS[lady.outfit.accessory].spells;
  if (spells.length === 0) return null;
  if (difficulty === NpcDifficulty.SQUIRE) return Math.random() < 0.33 ? pick(spells) : null;
  if (difficulty === NpcDifficulty.KNIGHT) {
    if (ownKnightHpRatio < 0.4 && spells.includes(SpellType.HEAL)) return SpellType.HEAL;
    return Math.random() < 0.5 ? pick(spells) : null;
  }
  if (ownKnightHpRatio < 0.35 && spells.includes(SpellType.HEAL)) return SpellType.HEAL;
  if (ownKnightHpRatio < 0.5 && spells.includes(SpellType.SHIELD)) return SpellType.SHIELD;
  if (enemyKnightHpRatio > 0.7 && spells.includes(SpellType.WEAKEN)) return SpellType.WEAKEN;
  if (spells.includes(SpellType.STRENGTH)) return SpellType.STRENGTH;
  return pick(spells);
}
