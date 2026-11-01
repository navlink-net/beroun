// Single-player tournament: the rules of the multiplayer server (TournamentRoom) without sockets, timers
// or a lobby. The human plays one knight or one lady, the other fifteen are NPCs. The UI steps everything.
import {
  ArmorType, HorseType, WeaponType, MedicineType, DressType, AccessoryType, PotionType, SpellType,
  Equipment, LadyOutfit, KnightPlayer, LadyPlayer, TournamentState, BattlePair, BattleTick,
  NpcDifficulty, KnightTitle, LadyTitle,
  ACCESSORY_STATS, SPELL_DEFINITIONS, MEDICINE_STATS, POTION_STATS, POINTS,
  computeKnightStats, getKnightTitle, getLadyTitle, getLadySkills,
} from './shared';
import { BattleEngine, BattleResult, SpellCast, ANIM_TICKS, LUCK_STEP, LUCK_MAX } from './battle';
import { NPC_KNIGHT_ROSTER, NPC_LADY_ROSTER, npcChoosePartners, npcDecideSpell } from './npc';

export const TOTAL_KNIGHTS = 8;
export const TOTAL_LADIES = 8;
export const TOTAL_ROUNDS = 7;

const K_DEF = 0.10, K_ATK = 0.08, K_HEALTH = 0.06, HEALTH_FLOOR = 0.10;
const LADY_PRIORITY_WEIGHTS = [1.0, 0.6, 0.3];
const STATUS_BASE_GAIN = 10, STATUS_BASE_LOSS = 7;

export interface Me { id: string; role: 'knight' | 'lady'; }

export interface BoutRecord {
  round: number;
  winnerId: string;
  loserId: string;
  winnerName: string;
  loserName: string;
  mineDelta: number | null;       // points the human gained or lost in this bout, null if not involved
}

export interface Bout {
  pair: BattlePair;
  k1: KnightPlayer;
  k2: KnightPlayer;
  ladies: LadyPlayer[];           // the ladies who back one of the two
  engine: BattleEngine;
  tick: BattleTick | null;
  mp: Record<string, number>;     // magic power at the start, restored if the app is closed mid-bout
  log: string[];                  // the spells cast, for the screen
}

export interface Saved { state: TournamentState; me: Me; log: BoutRecord[]; }

export class Tournament {
  log: BoutRecord[] = [];          // the bouts of the round in progress

  constructor(public state: TournamentState, public me: Me, log?: BoutRecord[]) {
    if (log) this.log = log;
  }

  // ─── creation ───────────────────────────────────────────────────────────────

  static create(role: 'knight' | 'lady', avatarNo: number, name: string): Tournament {
    const n = String(avatarNo).padStart(2, '0');
    const state: TournamentState = {
      roomCode: 'SOLO', hostId: 'me', phase: 'prepare', round: 1, totalRounds: TOTAL_ROUNDS,
      knights: [], ladies: [], schedule: [], currentBattleIndex: 0, currentBattle: null,
      prepareDeadline: null, nextBoutAt: null,
    };
    for (let i = 0; i < TOTAL_KNIGHTS; i++) {
      const t = NPC_KNIGHT_ROSTER[i];
      state.knights.push({
        id: `npc_k${i + 1}`, name: t.name, isNPC: true, npcDifficulty: NpcDifficulty.SQUIRE, role: 'knight',
        points: 0, isConnected: true, lobbyReady: true,
        equipment: { ...t.equipment }, baseStats: { ...t.baseStats },
        chosenLadies: [null, null, null], title: KnightTitle.SQUIRE, prepareReady: true,
        avatarUrl: t.avatarUrl, healthWorn: 1, armorWornPct: 1, weaponWornPct: 1,
        capturedArmors: [], capturedHorses: [], wins: 0, bouts: 0,
      });
    }
    for (let i = 0; i < TOTAL_LADIES; i++) {
      const t = NPC_LADY_ROSTER[i];
      state.ladies.push({
        id: `npc_l${i + 1}`, name: t.name, isNPC: true, npcDifficulty: NpcDifficulty.SQUIRE, role: 'lady',
        points: 0, isConnected: true, lobbyReady: true,
        outfit: { ...t.outfit }, magicPower: t.magicPower, maxMagicPower: t.maxMagicPower,
        chosenKnights: [null, null, null], title: LadyTitle.MAIDEN, prepareReady: true,
        avatarUrl: t.avatarUrl, status: 0,
      });
    }
    // the human takes the place of the NPC who wears the chosen picture
    const url = `assets/avatar/${role === 'knight' ? 'k' : 'l'}${n}.png`;
    if (role === 'knight') {
      const eq: Equipment = { armor: ArmorType.CHAINMAIL, horse: HorseType.COURSER, weapon: WeaponType.SWORD, medicine: MedicineType.HERBS };
      const idx = state.knights.findIndex(k => k.avatarUrl && k.avatarUrl.endsWith(`k${n}.png`));
      const me: KnightPlayer = {
        id: 'me', name, isNPC: false, role: 'knight', points: 0, isConnected: true, lobbyReady: true,
        equipment: eq, baseStats: computeKnightStats(eq), chosenLadies: [null, null, null],
        title: KnightTitle.SQUIRE, prepareReady: false, avatarUrl: url,
        healthWorn: 1, armorWornPct: 1, weaponWornPct: 1, capturedArmors: [], capturedHorses: [], wins: 0, bouts: 0,
      };
      state.knights[idx >= 0 ? idx : 0] = me;
    } else {
      const idx = state.ladies.findIndex(l => l.avatarUrl && l.avatarUrl.endsWith(`l${n}.png`));
      const me: LadyPlayer = {
        id: 'me', name, isNPC: false, role: 'lady', points: 0, isConnected: true, lobbyReady: true,
        outfit: { dress: DressType.NOBLE, accessory: AccessoryType.AMULET, potion: PotionType.MANA_BREW, medicine: MedicineType.HERBS },
        magicPower: 130, maxMagicPower: 130, chosenKnights: [null, null, null],
        title: LadyTitle.MAIDEN, prepareReady: false, avatarUrl: url, status: 0,
      };
      state.ladies[idx >= 0 ? idx : 0] = me;
    }
    const t = new Tournament(state, { id: 'me', role });
    t.buildSchedule();
    t.beginPrepare();
    return t;
  }

  get meK(): KnightPlayer | undefined { return this.state.knights.find(k => k.id === this.me.id); }
  get meL(): LadyPlayer | undefined { return this.state.ladies.find(l => l.id === this.me.id); }
  get player(): KnightPlayer | LadyPlayer { return (this.meK ?? this.meL)!; }

  knight(id: string): KnightPlayer { return this.state.knights.find(k => k.id === id)!; }
  lady(id: string): LadyPlayer { return this.state.ladies.find(l => l.id === id)!; }

  // Berger round-robin: every pair meets once over seven rounds
  private buildSchedule(): void {
    const ids = this.state.knights.map(k => k.id), N = ids.length;
    const rot = ids.slice(0, N - 1), fixed = ids[N - 1], pairs: BattlePair[] = [];
    let seq = 0;
    for (let round = 1; round <= TOTAL_ROUNDS; round++) {
      pairs.push({ id: `p${++seq}`, round, knight1Id: fixed, knight2Id: rot[0], completed: false });
      for (let k = 1; k < N / 2; k++) pairs.push({ id: `p${++seq}`, round, knight1Id: rot[k], knight2Id: rot[N - 1 - k], completed: false });
      rot.unshift(rot.pop()!);
    }
    this.state.schedule = pairs;
  }

  // ─── prepare phase ──────────────────────────────────────────────────────────

  beginPrepare(): void {
    this.state.phase = 'prepare';
    this.state.currentBattle = null;
    this.log = [];
    for (const p of [...this.state.knights, ...this.state.ladies]) p.prepareReady = p.isNPC;
    for (const k of this.state.knights) k.chosenLadies = [null, null, null];
    for (const l of this.state.ladies) { l.chosenKnights = [null, null, null]; l.magicPower = l.maxMagicPower; }
    for (const k of this.state.knights) {
      const bonus = MEDICINE_STATS[k.equipment.medicine].hpBonus / 100;
      k.healthWorn = Math.min(1.0, k.healthWorn + bonus);
    }
    for (const k of this.state.knights) if (k.isNPC) k.chosenLadies = npcChoosePartners(this.state.ladies, k.npcDifficulty ?? NpcDifficulty.SQUIRE);
    for (const l of this.state.ladies) if (l.isNPC) l.chosenKnights = npcChoosePartners(this.state.knights, l.npcDifficulty ?? NpcDifficulty.SQUIRE);
  }

  setEquipment(equipment: Equipment): void {
    const knight = this.meK;
    if (!knight) return;
    // switching armor: a captured copy brings its own wear, a standard one is new
    if (equipment.armor !== knight.equipment.armor) {
      const captured = knight.capturedArmors.find(a => a.type === equipment.armor);
      if (captured) {
        knight.armorWornPct = captured.wornPct;
        knight.capturedArmors.splice(knight.capturedArmors.indexOf(captured), 1);
      } else knight.armorWornPct = 1.0;
    }
    if (equipment.weapon !== knight.equipment.weapon) knight.weaponWornPct = 1.0;
    knight.equipment = equipment;
    knight.baseStats = computeKnightStats(equipment);
  }

  setOutfit(outfit: LadyOutfit): void {
    const lady = this.meL;
    if (!lady) return;
    lady.outfit = outfit;
    let max = 100;
    if (outfit.dress === DressType.MYSTIC) max += 20;
    max += POTION_STATS[outfit.potion].mpBonus;
    lady.maxMagicPower = max; lady.magicPower = max;
  }

  setPartners(ids: [string | null, string | null, string | null]): void {
    const k = this.meK, l = this.meL;
    if (k) k.chosenLadies = ids; else if (l) l.chosenKnights = ids;
  }

  // ─── bouts ──────────────────────────────────────────────────────────────────

  roundPairs(): BattlePair[] { return this.state.schedule.filter(p => p.round === this.state.round); }
  nextPair(): BattlePair | undefined { return this.roundPairs().find(p => !p.completed); }

  // does the human have a say in this bout (their knight fights, or their lady backs a fighter)?
  // the fighter of this bout that is the human's (their knight, or the first knight their lady backs), or null
  mineIn(pair: BattlePair): string | null {
    if (this.me.role === 'knight') return pair.knight1Id === this.me.id || pair.knight2Id === this.me.id ? this.me.id : null;
    return this.meL!.chosenKnights.find(id => id === pair.knight1Id || id === pair.knight2Id) ?? null;
  }
  // luck: after lost bouts in a row the human's fighter gets a little more chance (LUCK_STEP each, at most LUCK_MAX)
  favourFor(pair: BattlePair): { id: string; bonus: number } | null {
    const id = this.mineIn(pair), n = this.state.luckStreak || 0;
    return id && n > 0 ? { id, bonus: Math.min(LUCK_MAX, LUCK_STEP * n) } : null;
  }
  isLive(pair: BattlePair): boolean {
    if (this.me.role === 'knight') return pair.knight1Id === this.me.id || pair.knight2Id === this.me.id;
    const l = this.meL!;
    return l.chosenKnights.includes(pair.knight1Id) || l.chosenKnights.includes(pair.knight2Id);
  }

  makeBout(pair: BattlePair): Bout {
    const k1 = this.knight(pair.knight1Id), k2 = this.knight(pair.knight2Id);
    const ladies = this.state.ladies.filter(l => l.chosenKnights.includes(k1.id) || l.chosenKnights.includes(k2.id));
    const engine = new BattleEngine(k1, k2, ladies);
    const fav = this.favourFor(pair);
    if (fav) engine.setFavour(fav.id, fav.bonus);
    engine.begin();
    const mp: Record<string, number> = {};
    for (const l of ladies) mp[l.id] = l.magicPower;
    this.state.phase = 'battle';
    return { pair, k1, k2, ladies, engine, tick: null, mp, log: [] };
  }

  // one step of a bout; the NPC ladies cast after the tick, like the server did
  stepBout(b: Bout): BattleTick {
    const tick = b.engine.step();
    b.tick = tick;
    if (!tick.finished) this.npcCast(b, tick);
    return tick;
  }

  // a bout the human does not see: the same steps, in a loop
  quickBout(pair: BattlePair): BoutRecord {
    const b = this.makeBout(pair);
    while (!this.stepBout(b).finished) { /* steps */ }
    return this.finishBout(b);
  }

  finishBout(b: Bout): BoutRecord {
    const result = b.engine.result();
    const before = this.pointsOfMe();
    b.pair.completed = true; b.pair.winnerId = result.winnerId; b.pair.loserId = result.loserId;
    this.applyBattleResult(result);
    const after = this.pointsOfMe();
    const involved = this.isLive(b.pair);
    const rec: BoutRecord = {
      round: this.state.round, winnerId: result.winnerId, loserId: result.loserId,
      winnerName: this.knight(result.winnerId).name, loserName: this.knight(result.loserId).name,
      mineDelta: involved ? Math.round(after - before) : null,
    };
    this.log.push(rec);
    if (involved) this.state.luckStreak = result.winnerId === this.mineIn(b.pair) ? 0 : (this.state.luckStreak || 0) + 1;
    this.state.phase = 'standings';
    return rec;
  }

  private pointsOfMe(): number { return this.player.points; }

  castSpell(b: Bout, spellType: SpellType, targetKnightId: string): { ok: boolean; reason?: string } {
    const lady = this.meL;
    if (!lady) return { ok: false, reason: 'Only a lady casts spells' };
    const r = b.engine.queueSpell({ ladyId: lady.id, spellType, targetKnightId } as SpellCast);
    if (r.ok) b.log.push(`You cast ${SPELL_DEFINITIONS[spellType].name} on ${this.knight(targetKnightId).name}`);
    return r;
  }

  // a bout that was open when the app closed starts again: the spent magic comes back
  abandonBout(b: Bout): void { for (const l of b.ladies) l.magicPower = b.mp[l.id] ?? l.magicPower; this.state.phase = 'prepare'; }

  private npcCast(b: Bout, tick: BattleTick): void {
    for (const lady of b.ladies) {
      if (!lady.isNPC) continue;
      const ownId = lady.chosenKnights[0] ?? lady.chosenKnights[1] ?? lady.chosenKnights[2];
      if (!ownId) continue;
      const own = ownId === b.k1.id ? b.k1 : b.k2, enemy = ownId === b.k1.id ? b.k2 : b.k1;
      const ownHp = ownId === b.k1.id ? tick.knight1.currentHp : tick.knight2.currentHp;
      const enemyHp = ownId === b.k1.id ? tick.knight2.currentHp : tick.knight1.currentHp;
      const spell = npcDecideSpell(lady, ownHp / own.baseStats.maxHp, enemyHp / enemy.baseStats.maxHp, lady.npcDifficulty ?? NpcDifficulty.SQUIRE);
      if (!spell) continue;
      const def = SPELL_DEFINITIONS[spell];
      if (!ACCESSORY_STATS[lady.outfit.accessory].spells.includes(spell)) continue;
      if (lady.magicPower < def.powerCost) continue;
      const target = def.targetsSelf ? own : enemy;
      if (b.engine.queueSpell({ ladyId: lady.id, spellType: spell, targetKnightId: target.id }).ok) b.log.push(`${lady.name} casts ${def.name} on ${target.name}`);
    }
  }

  // ─── scoring (the server's rules; the hidden +2 it gave humans is not here) ─

  private applyBattleResult(result: BattleResult): void {
    const winner = this.knight(result.winnerId), loser = this.knight(result.loserId);
    const toWinner = Number.isFinite(result.damageToWinner) ? result.damageToWinner : 0;
    const toLoser = Number.isFinite(result.damageToLoser) ? result.damageToLoser : 0;

    winner.points += POINTS.WIN + Math.round(toLoser - toWinner);
    loser.points += Math.round(toWinner - toLoser);
    winner.wins++; winner.bouts++; loser.bouts++;
    winner.title = getKnightTitle(winner.points); loser.title = getKnightTitle(loser.points);

    winner.armorWornPct = Math.max(0, winner.armorWornPct - toWinner * K_DEF);
    winner.weaponWornPct = Math.max(0, winner.weaponWornPct - toWinner * K_ATK);
    loser.armorWornPct = Math.max(0, loser.armorWornPct - toLoser * K_DEF);
    loser.weaponWornPct = Math.max(0, loser.weaponWornPct - toLoser * K_ATK);
    loser.healthWorn = Math.max(HEALTH_FLOOR, loser.healthWorn - toLoser * K_HEALTH);

    // loot: the loser's armor and horse go to the winner's stable, the loser falls back to a spare or the basics
    if (loser.equipment.armor !== ArmorType.LEATHER) {
      winner.capturedArmors.push({ type: loser.equipment.armor, wornPct: loser.armorWornPct });
      const spare = loser.capturedArmors.shift();
      if (spare) { loser.equipment = { ...loser.equipment, armor: spare.type }; loser.armorWornPct = spare.wornPct; }
      else { loser.equipment = { ...loser.equipment, armor: ArmorType.LEATHER }; loser.armorWornPct = 0.5; }
    }
    if (loser.equipment.horse !== HorseType.ROUNCY) {
      winner.capturedHorses.push({ type: loser.equipment.horse });
      const spare = loser.capturedHorses.shift();
      loser.equipment = { ...loser.equipment, horse: spare ? spare.type : HorseType.ROUNCY };
    }

    for (const lady of this.state.ladies) {
      let delta = 0;
      const spellBonus = (result.spellsCastByLady.get(lady.id) ?? 0) * POINTS.SPELL_BONUS;
      for (let i = 0; i < 3; i++) {
        const id = lady.chosenKnights[i];
        if (!id) continue;
        if (id === result.winnerId) delta += POINTS.LADY_WIN_PRIORITY[i] + spellBonus;
        else if (id === result.loserId) delta += POINTS.LADY_LOSS_PRIORITY[i];
      }
      lady.points = Math.max(0, lady.points + delta);
      lady.title = getLadyTitle(lady.points);
      const s = getLadySkills(lady.avatarUrl);
      for (let i = 0; i < 3; i++) {
        const id = lady.chosenKnights[i];
        if (!id) continue;
        const w = LADY_PRIORITY_WEIGHTS[i];
        if (id === result.winnerId) lady.status = Math.min(100, lady.status + STATUS_BASE_GAIN * (1 + s.cha * 0.20) * w);
        else if (id === result.loserId) lady.status = Math.max(0, lady.status - STATUS_BASE_LOSS * w);
      }
    }
  }

  // ─── rounds ─────────────────────────────────────────────────────────────────

  // all bouts of the round are over: the next round, or the end
  endRound(): void {
    if (this.state.round >= TOTAL_ROUNDS) { this.state.phase = 'finished'; return; }
    this.state.round++;
    this.beginPrepare();
  }

  knightsRanked(): KnightPlayer[] { return [...this.state.knights].sort((a, b) => b.points - a.points || b.wins - a.wins); }
  ladiesRanked(): LadyPlayer[] { return [...this.state.ladies].sort((a, b) => b.points - a.points); }

  save(): Saved { return { state: this.state, me: this.me, log: this.log }; }
  static restore(s: Saved): Tournament { return new Tournament(s.state, s.me, s.log); }
}

export { ANIM_TICKS };
export * from './shared';
export { TICK_MS } from './battle';
export { NPC_KNIGHT_ROSTER, NPC_LADY_ROSTER } from './npc';
