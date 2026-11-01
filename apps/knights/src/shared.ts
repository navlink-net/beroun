// ─── Enums ───────────────────────────────────────────────────────────────────

export enum ArmorType {
  LEATHER   = 'leather',
  CHAINMAIL = 'chainmail',
  PLATE     = 'plate',
  FULLPLATE = 'fullplate',
}

export enum HorseType {
  ROUNCY   = 'rouncy',
  DESTRIER = 'destrier',
  COURSER  = 'courser',
  PALFREY  = 'palfrey',
}

export enum WeaponType {
  SWORD = 'sword',
  LANCE = 'lance',
  MACE  = 'mace',
  AXE   = 'axe',
}

export enum MedicineType {
  HERBS  = 'herbs',
  TONIC  = 'tonic',
  POTION = 'potion',
  ELIXIR = 'elixir',
}

export enum DressType {
  ROYAL     = 'royal',
  NOBLE     = 'noble',
  MYSTIC    = 'mystic',
  ENCHANTED = 'enchanted',
}

export enum AccessoryType {
  WAND   = 'wand',
  AMULET = 'amulet',
  MIRROR = 'mirror',
  CROWN  = 'crown',
}

export enum PotionType {
  MANA_BREW  = 'mana_brew',
  CLARITY    = 'clarity',
  AMPLIFIER  = 'amplifier',
  VENOM      = 'venom',
}

export enum SpellType {
  HEAL     = 'heal',
  STRENGTH = 'strength',
  SHIELD   = 'shield',
  WEAKEN   = 'weaken',
  CURSE    = 'curse',
  REFLECT  = 'reflect',
  INSPIRE  = 'inspire',
  DISPEL   = 'dispel',
}

export enum KnightTitle {
  SQUIRE = 'Squire',
  KNIGHT = 'Knight',
  CHAMPION = 'Champion',
  PRINCE = 'Prince',
}

export enum LadyTitle {
  MAIDEN = 'Maiden',
  COURT_LADY = 'Court Lady',
  FAVORITE = 'Favorite',
  PRINCESS = 'Princess',
}

export enum NpcDifficulty {
  SQUIRE = 'squire',
  KNIGHT = 'knight',
  CHAMPION = 'champion',
}

// ─── Stat Tables ─────────────────────────────────────────────────────────────

export interface ArmorStats   { defense: number; speedMod: number; }
export interface HorseStats   { speed: number; powerMod: number; }
export interface WeaponStats  { attack: number; special: 'none' | 'first_strike' | 'pierce' | 'slow'; }
export interface MedicineStats { hpBonus: number; }

export const ARMOR_STATS: Record<ArmorType, ArmorStats> = {
  [ArmorType.LEATHER]:   { defense: 2,  speedMod: 2  },
  [ArmorType.CHAINMAIL]: { defense: 5,  speedMod: 0  },
  [ArmorType.PLATE]:     { defense: 8,  speedMod: -2 },
  [ArmorType.FULLPLATE]: { defense: 12, speedMod: -4 },
};

export const HORSE_STATS: Record<HorseType, HorseStats> = {
  [HorseType.ROUNCY]:   { speed: 1, powerMod: 3  },
  [HorseType.DESTRIER]: { speed: 3, powerMod: 2  },
  [HorseType.COURSER]:  { speed: 5, powerMod: 0  },
  [HorseType.PALFREY]:  { speed: 7, powerMod: -2 },
};

export const WEAPON_STATS: Record<WeaponType, WeaponStats> = {
  [WeaponType.SWORD]: { attack: 5, special: 'none'         },
  [WeaponType.LANCE]: { attack: 7, special: 'first_strike' },
  [WeaponType.MACE]:  { attack: 6, special: 'pierce'       },
  [WeaponType.AXE]:   { attack: 9, special: 'slow'         },
};

export const MEDICINE_STATS: Record<MedicineType, MedicineStats> = {
  [MedicineType.HERBS]:  { hpBonus: 15 },
  [MedicineType.TONIC]:  { hpBonus: 25 },
  [MedicineType.POTION]: { hpBonus: 40 },
  [MedicineType.ELIXIR]: { hpBonus: 60 },
};

export interface DressStats {
  atkWeight: number;  // multiplier on lady's atk influence (ROYAL=1.6, NOBLE=0.4, others ~0.8-0.9)
  defWeight: number;  // multiplier on lady's def influence (ROYAL=0.4, NOBLE=1.6, others ~0.8-0.9)
  effect: 'attack_focused' | 'defense_focused' | 'magic_bonus' | 'spell_discount';
}
export interface AccessoryStats { spells: SpellType[]; }
export interface PotionStats    { mpBonus: number; spellCostDiscount: number; buffDurationBonus: number; debuffDurationBonus: number; }

export const DRESS_STATS: Record<DressType, DressStats> = {
  [DressType.ROYAL]:     { atkWeight: 1.6, defWeight: 0.4, effect: 'attack_focused'  },
  [DressType.NOBLE]:     { atkWeight: 0.4, defWeight: 1.6, effect: 'defense_focused' },
  [DressType.MYSTIC]:    { atkWeight: 0.8, defWeight: 0.8, effect: 'magic_bonus'     },
  [DressType.ENCHANTED]: { atkWeight: 0.9, defWeight: 0.9, effect: 'spell_discount'  },
};

export const ACCESSORY_STATS: Record<AccessoryType, AccessoryStats> = {
  [AccessoryType.WAND]:   { spells: [SpellType.STRENGTH, SpellType.WEAKEN]  },
  [AccessoryType.AMULET]: { spells: [SpellType.HEAL,     SpellType.SHIELD]  },
  [AccessoryType.MIRROR]: { spells: [SpellType.CURSE,    SpellType.REFLECT] },
  [AccessoryType.CROWN]:  { spells: [SpellType.INSPIRE,  SpellType.DISPEL]  },
};

export const POTION_STATS: Record<PotionType, PotionStats> = {
  [PotionType.MANA_BREW]:  { mpBonus: 30, spellCostDiscount: 0, buffDurationBonus: 0, debuffDurationBonus: 0 },
  [PotionType.CLARITY]:    { mpBonus:  0, spellCostDiscount: 5, buffDurationBonus: 0, debuffDurationBonus: 0 },
  [PotionType.AMPLIFIER]:  { mpBonus:  0, spellCostDiscount: 0, buffDurationBonus: 1, debuffDurationBonus: 0 },
  [PotionType.VENOM]:      { mpBonus:  0, spellCostDiscount: 0, buffDurationBonus: 0, debuffDurationBonus: 1 },
};

// ─── Spell Definitions ───────────────────────────────────────────────────────

export interface SpellDefinition {
  type: SpellType;
  name: string;
  powerCost: number;
  duration: number;
  description: string;
  targetsSelf: boolean;
}

export const SPELL_DEFINITIONS: Record<SpellType, SpellDefinition> = {
  [SpellType.HEAL]: {
    type: SpellType.HEAL, name: 'Healing', powerCost: 30, duration: 0,
    description: 'Stamina +6%', targetsSelf: true,
  },
  [SpellType.SHIELD]: {
    type: SpellType.SHIELD, name: 'Shield', powerCost: 25, duration: 3,
    description: 'Defense +8%', targetsSelf: true,
  },
  [SpellType.STRENGTH]: {
    type: SpellType.STRENGTH, name: 'Strength', powerCost: 25, duration: 3,
    description: 'Attack +8%', targetsSelf: true,
  },
  [SpellType.WEAKEN]: {
    type: SpellType.WEAKEN, name: 'Weaken', powerCost: 35, duration: 3,
    description: 'Enemy attack -8%', targetsSelf: false,
  },
  [SpellType.CURSE]: {
    type: SpellType.CURSE, name: 'Curse', powerCost: 35, duration: 3,
    description: 'Enemy defense -8%', targetsSelf: false,
  },
  [SpellType.REFLECT]: {
    type: SpellType.REFLECT, name: 'Reflect', powerCost: 40, duration: 1,
    description: 'Halves the damage he takes', targetsSelf: true,
  },
  [SpellType.INSPIRE]: {
    type: SpellType.INSPIRE, name: 'Inspire', powerCost: 40, duration: 2,
    description: 'Attack +12%', targetsSelf: true,
  },
  [SpellType.DISPEL]: {
    type: SpellType.DISPEL, name: 'Dispel', powerCost: 30, duration: 0,
    description: "Removes the enemy's boosts", targetsSelf: false,
  },
};

// ─── Avatar Skill Profiles ────────────────────────────────────────────────────
// Values are 0-5 (direct icon count). Sum = 9 for all profiles.

export interface KnightSkills { atk: number; def: number; sta: number; }
export interface LadySkills   { mag: number; inf: number; cha: number; }

export const KNIGHT_AVATAR_SKILLS: readonly KnightSkills[] = [
  { atk: 3, def: 3, sta: 3 }, // k01 English   — Balanced
  { atk: 2, def: 5, sta: 2 }, // k02 French    — Defensive
  { atk: 1, def: 5, sta: 3 }, // k03 German    — Heavy
  { atk: 5, def: 1, sta: 3 }, // k04 Moorish   — Aggressive
  { atk: 2, def: 3, sta: 4 }, // k05 Byzantine — Resilient
  { atk: 5, def: 2, sta: 2 }, // k06 Norse     — Berserker
  { atk: 3, def: 2, sta: 4 }, // k07 Scottish  — Enduring
  { atk: 5, def: 3, sta: 1 }, // k08 Saracen   — Swift
];

export const LADY_AVATAR_SKILLS: readonly LadySkills[] = [
  { mag: 3, inf: 3, cha: 3 }, // l01 English   — Balanced
  { mag: 1, inf: 5, cha: 3 }, // l02 French    — Influential
  { mag: 3, inf: 2, cha: 4 }, // l03 German    — Charismatic
  { mag: 5, inf: 1, cha: 3 }, // l04 Moorish   — Arcane
  { mag: 2, inf: 5, cha: 2 }, // l05 Byzantine — Diplomatic
  { mag: 2, inf: 3, cha: 4 }, // l06 Norse     — Charming
  { mag: 3, inf: 1, cha: 5 }, // l07 Scottish  — Radiant
  { mag: 5, inf: 3, cha: 1 }, // l08 Persian   — Mystic
];

// ─── Player Types ─────────────────────────────────────────────────────────────

export interface Equipment {
  armor:    ArmorType;
  horse:    HorseType;
  weapon:   WeaponType;
  medicine: MedicineType;
}

export interface LadyOutfit {
  dress:     DressType;
  accessory: AccessoryType;
  potion:    PotionType;
  medicine:  MedicineType;
}

export interface CapturedArmor {
  type:    ArmorType;
  wornPct: number;  // 0-1; state at capture time
}

export interface CapturedHorse {
  type: HorseType;
}

export interface KnightStats {
  maxHp: number;
  attack: number;
  defense: number;
  speed: number;
}

// ─── Avatar skill helpers ─────────────────────────────────────────────────────

export function getKnightSkills(avatarUrl?: string): KnightSkills {
  if (!avatarUrl) return KNIGHT_AVATAR_SKILLS[0];
  const m = avatarUrl.match(/k(\d+)\.png/i);
  if (!m) return KNIGHT_AVATAR_SKILLS[0];
  return KNIGHT_AVATAR_SKILLS[parseInt(m[1]) - 1] ?? KNIGHT_AVATAR_SKILLS[0];
}

export function getLadySkills(avatarUrl?: string): LadySkills {
  if (!avatarUrl) return LADY_AVATAR_SKILLS[0];
  const m = avatarUrl.match(/l(\d+)\.png/i);
  if (!m) return LADY_AVATAR_SKILLS[0];
  return LADY_AVATAR_SKILLS[parseInt(m[1]) - 1] ?? LADY_AVATAR_SKILLS[0];
}

export interface BasePlayer {
  id: string;
  username?: string;
  name: string;
  isNPC: boolean;
  npcDifficulty?: NpcDifficulty;
  points: number;
  isConnected: boolean;
  lobbyReady: boolean;
  avatarUrl?: string;
}

export interface KnightPlayer extends BasePlayer {
  role: 'knight';
  equipment: Equipment;
  baseStats: KnightStats;
  chosenLadies: [string | null, string | null, string | null];
  title: KnightTitle;
  prepareReady: boolean;
  // Persistent degradation (0-1 scale; 1.0 = pristine/full)
  healthWorn:    number;
  armorWornPct:  number;
  weaponWornPct: number;
  // Loot captured from defeated opponents
  capturedArmors: CapturedArmor[];
  capturedHorses: CapturedHorse[];
  wins:  number;
  bouts: number;
}

export interface LadyPlayer extends BasePlayer {
  role: 'lady';
  outfit: LadyOutfit;
  magicPower: number;
  maxMagicPower: number;
  chosenKnights: [string | null, string | null, string | null];
  title: LadyTitle;
  prepareReady: boolean;
  status: number;  // 0-100 (percentage); rises with knight wins, falls with losses
}

export type Player = KnightPlayer | LadyPlayer;

// ─── Battle Types ─────────────────────────────────────────────────────────────

export interface ActiveEffect {
  id: string;
  type: SpellType;
  targetKnightId: string;
  remainingTicks: number;
  attackMod: number;
  defenseMod: number;
  reflectActive: boolean;
  sourceLadyId: string;
}

export interface BattleEventEntry {
  tick: number;
  type: 'attack' | 'spell_cast' | 'effect_expire' | 'battle_end' | 'heal';
  actorId?: string;
  targetId?: string;
  damage?: number;
  heal?: number;
  spell?: SpellType;
  message: string;
}

export interface KnightBattleState {
  id: string;
  currentHp: number;
  maxHp: number;
  isAttacking: boolean;
  isHurt: boolean;
}

export interface BattleTick {
  tick: number;
  knight1: KnightBattleState;
  knight2: KnightBattleState;
  activeEffects: ActiveEffect[];
  events: BattleEventEntry[];
  finished: boolean;
  winnerId?: string;
  loserId?: string;
}

export interface BattlePair {
  id: string;
  round: number;
  knight1Id: string;
  knight2Id: string;
  completed: boolean;
  winnerId?: string;
  loserId?: string;
}

export interface ActiveBattle {
  pair: BattlePair;
  currentTick: BattleTick;
}

// ─── Tournament State ─────────────────────────────────────────────────────────

export type TournamentPhase = 'lobby' | 'prepare' | 'battle' | 'standings' | 'finished';

export interface TournamentState {
  roomCode: string;
  hostId: string;
  phase: TournamentPhase;
  round: number;
  totalRounds: number;
  knights: KnightPlayer[];
  ladies: LadyPlayer[];
  schedule: BattlePair[];
  currentBattleIndex: number;
  currentBattle: ActiveBattle | null;
  prepareDeadline: number | null;
  nextBoutAt: number | null;
  luckStreak?: number;   // the human's lost bouts in a row (single player; see LUCK_STEP in battle.ts)
}

// ─── Scoring helpers (used by server and displayed on client) ─────────────────

export const POINTS = {
  WIN: 15,
  LOSS: -5,
  LADY_WIN_PRIORITY: [12, 6, 2] as const,
  LADY_LOSS_PRIORITY: [-4, -1, 0] as const,
  SPELL_BONUS: 2,
  ROYAL_DRESS_MULTIPLIER: 1.2,
  NOBLE_DRESS_LOSS_REDUCTION: 1,
} as const;

export function getKnightTitle(points: number): KnightTitle {
  if (points >= 300) return KnightTitle.PRINCE;
  if (points >= 151) return KnightTitle.CHAMPION;
  if (points >= 51)  return KnightTitle.KNIGHT;
  return KnightTitle.SQUIRE;
}

export function getLadyTitle(points: number): LadyTitle {
  if (points >= 300) return LadyTitle.PRINCESS;
  if (points >= 151) return LadyTitle.FAVORITE;
  if (points >= 51)  return LadyTitle.COURT_LADY;
  return LadyTitle.MAIDEN;
}

export function computeKnightStats(eq: Equipment): KnightStats {
  const armor    = ARMOR_STATS[eq.armor];
  const horse    = HORSE_STATS[eq.horse];
  const weapon   = WEAPON_STATS[eq.weapon];
  const medicine = MEDICINE_STATS[eq.medicine];
  return {
    maxHp:   100 + medicine.hpBonus,
    attack:  10  + weapon.attack + horse.powerMod,
    defense: 5   + armor.defense,
    speed:   5   + horse.speed   + armor.speedMod,
  };
}

// ─── Socket Event Maps ────────────────────────────────────────────────────────

export interface ClientToServerEvents {
  'player:join': (data: { roomCode: string; name: string; role: 'knight' | 'lady'; session: string; avatarUrl?: string }) => void;
  'lobby:ready': () => void;
  'prepare:equipment': (equipment: Equipment) => void;
  'prepare:outfit': (outfit: LadyOutfit) => void;
  'prepare:partners': (partnerIds: [string | null, string | null, string | null]) => void;
  'prepare:precast': (data: { spellType: SpellType; targetKnightId: string }) => void;
  'prepare:ready': () => void;
  'battle:spell': (data: { spellType: SpellType; targetKnightId: string }) => void;
  'standings:next':     () => void;
  'prepare:forcestart': () => void;
}

// ─── Account (persistent per-user attributes) ─────────────────────────────────

export interface KnightAccount {
  money: number;
  defenseStrength: number;
  attackStrength: number;
  healthPercent: number;
  totalGames: number;
  wins: number;
}

export interface LadyAccount {
  money: number;
  influenceStrength: number;
  magicStrength: number;
  status: number;
  totalGames: number;
  wins: number;
}

export interface AccountData {
  username: string;
  knight: KnightAccount;
  lady: LadyAccount;
}

export interface ServerToClientEvents {
  'room:joined': (data: { playerId: string; tournament: TournamentState; account: AccountData }) => void;
  'room:error': (message: string) => void;
  'tournament:update': (tournament: TournamentState) => void;
  'battle:tick': (tick: BattleTick) => void;
  'spell:result': (data: { success: boolean; spellType: SpellType; reason?: string }) => void;
}
