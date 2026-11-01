import {
  KnightPlayer, LadyPlayer,
  SpellType, ActiveEffect, BattleTick, BattleEventEntry,
  SPELL_DEFINITIONS, WEAPON_STATS, HORSE_STATS, ARMOR_STATS,
  PotionType, DressType, POTION_STATS, DRESS_STATS,
  getKnightSkills, getLadySkills,
} from './shared';

// ─── Tuning constants ─────────────────────────────────────────────────────────

export const ANIM_TICKS = 5;
export const TICK_MS = 1500;

// How a bout looks (the owner, 2026-10-29): both bars move, and the end is not visible from the first tick. The winner's and the loser's bars each follow one of a few scripts
// (the share of their damage taken by tick 1..5, the last is always 1), picked at random for the bout and not by who wins, so the bar that falls fastest is not always the loser.
export const WINNER_SCRIPTS: number[][] = [[0.65, 0.80, 0.90, 0.97, 1], [0.35, 0.60, 0.80, 0.92, 1], [0.50, 0.70, 0.85, 0.95, 1], [0.25, 0.50, 0.75, 0.90, 1]];
export const LOSER_SCRIPTS: number[][] = [[0.05, 0.10, 0.18, 0.30, 1], [0.15, 0.22, 0.32, 0.45, 1], [0.10, 0.20, 0.25, 0.40, 1], [0.20, 0.30, 0.45, 0.60, 1]];
// Luck (the owner, 2026-10-29): after each lost bout in a row the human's fighter gets a little more chance to win: LUCK_STEP per loss, at most LUCK_MAX, the chance never lifted above
// LUCK_CEILING; a win resets it. The tournament counts the losses (state.luckStreak) and tells the bout.
export const LUCK_STEP = 0.05;
export const LUCK_MAX = 0.20;
export const LUCK_CEILING = 0.85;

const PRIORITY_WEIGHTS = [1.0, 0.6, 0.3];

// Spell modifiers on effective stats (per cast, before mag scaling)
const SPELL_ATK_MOD: Partial<Record<SpellType, number>> = {
  [SpellType.STRENGTH]: 0.08,
  [SpellType.INSPIRE]:  0.12,
  [SpellType.WEAKEN]:  -0.08,
};
const SPELL_DEF_MOD: Partial<Record<SpellType, number>> = {
  [SpellType.SHIELD]:  0.08,
  [SpellType.CURSE]:  -0.08,
};
const SPELL_STA_MOD: Partial<Record<SpellType, number>> = {
  [SpellType.HEAL]:    0.06,
};

// ─── Types ────────────────────────────────────────────────────────────────────

export interface SpellCast {
  ladyId:         string;
  spellType:      SpellType;
  targetKnightId: string;
}

export interface BattleResult {
  winnerId:         string;
  loserId:          string;
  damageToWinner:   number;
  damageToLoser:    number;
  spellsCastByLady: Map<string, number>;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function computeLadyMults(
  knightId: string, ladies: LadyPlayer[],
): { atk: number; def: number } {
  let atkBonus = 0;
  let defBonus = 0;
  for (const lady of ladies) {
    const priIdx = lady.chosenKnights.indexOf(knightId);
    if (priIdx === -1) continue;
    const w     = PRIORITY_WEIGHTS[priIdx];
    const s     = getLadySkills(lady.avatarUrl);
    const d     = DRESS_STATS[lady.outfit.dress];
    const base  = (s.inf * 0.20 + (lady.status / 100) * 0.30) * w;
    atkBonus   += base * d.atkWeight;
    defBonus   += base * d.defWeight;
  }
  return { atk: 1 + atkBonus, def: 1 + defBonus };
}

function computeAtkEff(k: KnightPlayer, ladyAtkMult: number, atkSpellMod: number): number {
  const w = WEAPON_STATS[k.equipment.weapon];
  const h = HORSE_STATS[k.equipment.horse];
  const skills = getKnightSkills(k.avatarUrl);
  const base = (w.attack + h.powerMod) * k.weaponWornPct;
  return base * (1 + skills.atk * 0.20) * ladyAtkMult * (1 + atkSpellMod);
}

function computeDefEff(k: KnightPlayer, ladyDefMult: number, defSpellMod: number): number {
  const a = ARMOR_STATS[k.equipment.armor];
  const skills = getKnightSkills(k.avatarUrl);
  return a.defense * k.armorWornPct * (1 + skills.def * 0.20) * ladyDefMult * (1 + defSpellMod);
}

function computeStamina(k: KnightPlayer, staSpellMod: number): number {
  const skills = getKnightSkills(k.avatarUrl);
  return Math.max(0.10, k.healthWorn + staSpellMod) * (1 + skills.sta * 0.20);
}

// ─── BattleEngine ─────────────────────────────────────────────────────────────

export class BattleEngine {
  private tick = 0;
  private finished = false;

  // Spell accumulators: per-knight modifiers (fractional, e.g. 0.08 per cast)
  private atkMod: Record<string, number> = {};
  private defMod: Record<string, number> = {};
  private staMod: Record<string, number> = {};

  private pendingSpells: SpellCast[] = [];
  private spellsCastByLady = new Map<string, number>();

  // Resolved outcome (computed at start)
  private roll = Math.random();
  private reflect: Record<string, number> = {};
  private winner!: KnightPlayer;
  private loser!:  KnightPlayer;
  private damageToWinner = 0;
  private damageToLoser  = 0;
  private winProbability = 0;
  private shownHp = [100, 100];
  private favour: { id: string; bonus: number } | null = null;
  private scriptW = Math.floor(Math.random() * WINNER_SCRIPTS.length);
  private scriptL = Math.floor(Math.random() * LOSER_SCRIPTS.length);

  constructor(
    private knight1: KnightPlayer,
    private knight2: KnightPlayer,
    private ladies:  LadyPlayer[],
  ) {
    this.atkMod[knight1.id] = 0;
    this.atkMod[knight2.id] = 0;
    this.defMod[knight1.id] = 0;
    this.defMod[knight2.id] = 0;
    this.staMod[knight1.id] = 0;
    this.staMod[knight2.id] = 0;
  }

  // ─── outcome resolution ───────────────────────────────────────────────────

  // The winner follows one die rolled at the start of the bout against the winning chance as it stands now, so the
  // spells cast so far move the odds (the multiplayer build fixed the winner before any spell could count).
  private resolveOutcome(): void {
    const lm1 = computeLadyMults(this.knight1.id, this.ladies);
    const lm2 = computeLadyMults(this.knight2.id, this.ladies);

    const atk1 = computeAtkEff(this.knight1, lm1.atk, this.atkMod[this.knight1.id]);
    const atk2 = computeAtkEff(this.knight2, lm2.atk, this.atkMod[this.knight2.id]);
    const def1 = computeDefEff(this.knight1, lm1.def, this.defMod[this.knight1.id]);
    const def2 = computeDefEff(this.knight2, lm2.def, this.defMod[this.knight2.id]);
    const sta1 = computeStamina(this.knight1, this.staMod[this.knight1.id]);
    const sta2 = computeStamina(this.knight2, this.staMod[this.knight2.id]);

    const power1 = atk1 * sta1;
    const power2 = atk2 * sta2;

    const totalPower = power1 * power1 + power2 * power2;
    this.winProbability = totalPower > 0 ? (power1 * power1) / totalPower : 0.5;
    if (this.favour) {   // luck: the human's fighter has a little more chance after lost bouts in a row
      const mine1 = this.favour.id === this.knight1.id;
      const pm = mine1 ? this.winProbability : 1 - this.winProbability;
      const lifted = Math.max(pm, Math.min(LUCK_CEILING, pm + this.favour.bonus));
      this.winProbability = mine1 ? lifted : 1 - lifted;
    }

    const rolled = this.roll < this.winProbability;
    this.winner = rolled ? this.knight1 : this.knight2;
    this.loser  = rolled ? this.knight2 : this.knight1;

    const k1Wins = this.winner.id === this.knight1.id;
    const atkW = k1Wins ? atk1 : atk2;
    const atkL = k1Wins ? atk2 : atk1;
    const defW = k1Wins ? def1 : def2;
    const defL = k1Wins ? def2 : def1;
    const pW   = k1Wins ? this.winProbability : 1 - this.winProbability;

    // Reflect: every cast halves what the knight takes
    this.damageToLoser  = Math.max(0, atkW - defL) * Math.pow(0.5, this.reflect[this.loser.id] ?? 0);
    this.damageToWinner = Math.max(0, atkL - defW) * (1 - pW) * Math.pow(0.5, this.reflect[this.winner.id] ?? 0);
  }

  // ─── public API ───────────────────────────────────────────────────────────

  // The single-player build has no timers: the caller steps the bout (every TICK_MS for a bout shown on
  // the screen, in a loop for one that is only computed).
  private isK1Winner = false;
  private winnerFinalHp = 0;
  private readonly loserFinalHp = 4;

  // luck: the human's fighter and how much more chance it has (the tournament knows)
  setFavour(id: string, bonus: number): void {
    this.favour = bonus > 0 ? { id, bonus } : null;
  }

  // the chance that knight 1 wins as it stands now (the spells and the luck count): the screen shows it
  chance1(): number {
    return this.winProbability;
  }

  begin(): void {
    this.resolveOutcome();
    this.updateHp();
  }

  // the picture of the bout follows the outcome as it stands now
  private updateHp(): void {
    this.winnerFinalHp = Math.round(100 - (30 + Math.min(1, this.damageToWinner / Math.max(this.damageToLoser, 1)) * 30));
    this.isK1Winner = this.winner.id === this.knight1.id;
  }

  step(): BattleTick {
    this.tick++;
    this.flushSpells();                                  // spells queued since the last tick
    this.resolveOutcome();                               // the spells cast so far count
    this.updateHp();

    const winnerHp = Math.round(100 - (100 - this.winnerFinalHp) * WINNER_SCRIPTS[this.scriptW][Math.min(this.tick, ANIM_TICKS) - 1]);
    const loserHp  = Math.round(100 - (100 - this.loserFinalHp)  * LOSER_SCRIPTS[this.scriptL][Math.min(this.tick, ANIM_TICKS) - 1]);
    // a bar never grows back (the winner can change while the spells are cast: the new winner goes on from where its bar is)
    const k1Hp = Math.min(this.isK1Winner ? winnerHp : loserHp, this.shownHp[0]);
    const k2Hp = Math.min(this.isK1Winner ? loserHp : winnerHp, this.shownHp[1]);
    this.shownHp = [k1Hp, k2Hp];

    const isFinished = this.tick >= ANIM_TICKS;
    const events: BattleEventEntry[] = [];
    if (isFinished) {
      events.push({ tick: this.tick, type: 'battle_end', message: `${this.winner.name} wins!`, actorId: this.winner.id, targetId: this.loser.id });
      this.finished = true;
    }
    return {
      tick: this.tick,
      knight1: { id: this.knight1.id, currentHp: Math.max(0, k1Hp), maxHp: 100, isAttacking: !isFinished, isHurt: false },
      knight2: { id: this.knight2.id, currentHp: Math.max(0, k2Hp), maxHp: 100, isAttacking: !isFinished, isHurt: false },
      activeEffects: [],
      events,
      finished: isFinished,
      winnerId: this.winner.id,
      loserId:  this.loser.id,
    };
  }

  result(): BattleResult {
    return {
      winnerId:         this.winner.id,
      loserId:          this.loser.id,
      damageToWinner:   this.damageToWinner,
      damageToLoser:    this.damageToLoser,
      spellsCastByLady: this.spellsCastByLady,
    };
  }

  queueSpell(cast: SpellCast): { ok: boolean; reason?: string } {
    if (this.finished) return { ok: false, reason: 'Battle is over' };

    const lady = this.ladies.find(l => l.id === cast.ladyId);
    if (!lady) return { ok: false, reason: 'Not your battle' };

    let discount = 0;
    if (lady.outfit.potion === PotionType.CLARITY)  discount += POTION_STATS[PotionType.CLARITY].spellCostDiscount;
    if (lady.outfit.dress  === DressType.ENCHANTED)  discount += 5;
    const cost = Math.max(0, SPELL_DEFINITIONS[cast.spellType].powerCost - discount);
    if (lady.magicPower < cost) return { ok: false, reason: 'Not enough magic power' };

    lady.magicPower -= cost;
    this.pendingSpells.push(cast);
    return { ok: true };
  }

  // ─── internals ────────────────────────────────────────────────────────────

  private flushSpells(): void {
    for (const cast of this.pendingSpells) {
      const lady = this.ladies.find(l => l.id === cast.ladyId);
      const ladySkills = getLadySkills(lady?.avatarUrl);
      const magScale = 1 + ladySkills.mag * 0.20;

      const atkM = (SPELL_ATK_MOD[cast.spellType] ?? 0) * magScale;
      const defM = (SPELL_DEF_MOD[cast.spellType] ?? 0) * magScale;
      const staM = (SPELL_STA_MOD[cast.spellType] ?? 0) * magScale;

      const tid = cast.targetKnightId;
      if (cast.spellType === SpellType.REFLECT) this.reflect[tid] = (this.reflect[tid] ?? 0) + 1;
      if (cast.spellType === SpellType.DISPEL) {            // takes away what the knight has been given
        for (const m of [this.atkMod, this.defMod, this.staMod]) if (m[tid] !== undefined) m[tid] = Math.min(0, m[tid]);
      }
      if (this.atkMod[tid] !== undefined) this.atkMod[tid] += atkM;
      if (this.defMod[tid] !== undefined) this.defMod[tid] += defM;
      if (this.staMod[tid] !== undefined) this.staMod[tid] += staM;

      const count = this.spellsCastByLady.get(cast.ladyId) ?? 0;
      this.spellsCastByLady.set(cast.ladyId, count + 1);
    }
    this.pendingSpells = [];
  }
}
