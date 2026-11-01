// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
var Engine = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);

  // src/tournament.ts
  var tournament_exports = {};
  __export(tournament_exports, {
    ACCESSORY_STATS: () => ACCESSORY_STATS,
    ANIM_TICKS: () => ANIM_TICKS,
    ARMOR_STATS: () => ARMOR_STATS,
    AccessoryType: () => AccessoryType,
    ArmorType: () => ArmorType,
    DRESS_STATS: () => DRESS_STATS,
    DressType: () => DressType,
    HORSE_STATS: () => HORSE_STATS,
    HorseType: () => HorseType,
    KNIGHT_AVATAR_SKILLS: () => KNIGHT_AVATAR_SKILLS,
    KnightTitle: () => KnightTitle,
    LADY_AVATAR_SKILLS: () => LADY_AVATAR_SKILLS,
    LadyTitle: () => LadyTitle,
    MEDICINE_STATS: () => MEDICINE_STATS,
    MedicineType: () => MedicineType,
    NPC_KNIGHT_ROSTER: () => NPC_KNIGHT_ROSTER,
    NPC_LADY_ROSTER: () => NPC_LADY_ROSTER,
    NpcDifficulty: () => NpcDifficulty,
    POINTS: () => POINTS,
    POTION_STATS: () => POTION_STATS,
    PotionType: () => PotionType,
    SPELL_DEFINITIONS: () => SPELL_DEFINITIONS,
    SpellType: () => SpellType,
    TICK_MS: () => TICK_MS,
    TOTAL_KNIGHTS: () => TOTAL_KNIGHTS,
    TOTAL_LADIES: () => TOTAL_LADIES,
    TOTAL_ROUNDS: () => TOTAL_ROUNDS,
    Tournament: () => Tournament,
    WEAPON_STATS: () => WEAPON_STATS,
    WeaponType: () => WeaponType,
    computeKnightStats: () => computeKnightStats,
    getKnightSkills: () => getKnightSkills,
    getKnightTitle: () => getKnightTitle,
    getLadySkills: () => getLadySkills,
    getLadyTitle: () => getLadyTitle
  });

  // src/shared.ts
  var ArmorType = /* @__PURE__ */ ((ArmorType2) => {
    ArmorType2["LEATHER"] = "leather";
    ArmorType2["CHAINMAIL"] = "chainmail";
    ArmorType2["PLATE"] = "plate";
    ArmorType2["FULLPLATE"] = "fullplate";
    return ArmorType2;
  })(ArmorType || {});
  var HorseType = /* @__PURE__ */ ((HorseType2) => {
    HorseType2["ROUNCY"] = "rouncy";
    HorseType2["DESTRIER"] = "destrier";
    HorseType2["COURSER"] = "courser";
    HorseType2["PALFREY"] = "palfrey";
    return HorseType2;
  })(HorseType || {});
  var WeaponType = /* @__PURE__ */ ((WeaponType2) => {
    WeaponType2["SWORD"] = "sword";
    WeaponType2["LANCE"] = "lance";
    WeaponType2["MACE"] = "mace";
    WeaponType2["AXE"] = "axe";
    return WeaponType2;
  })(WeaponType || {});
  var MedicineType = /* @__PURE__ */ ((MedicineType2) => {
    MedicineType2["HERBS"] = "herbs";
    MedicineType2["TONIC"] = "tonic";
    MedicineType2["POTION"] = "potion";
    MedicineType2["ELIXIR"] = "elixir";
    return MedicineType2;
  })(MedicineType || {});
  var DressType = /* @__PURE__ */ ((DressType2) => {
    DressType2["ROYAL"] = "royal";
    DressType2["NOBLE"] = "noble";
    DressType2["MYSTIC"] = "mystic";
    DressType2["ENCHANTED"] = "enchanted";
    return DressType2;
  })(DressType || {});
  var AccessoryType = /* @__PURE__ */ ((AccessoryType2) => {
    AccessoryType2["WAND"] = "wand";
    AccessoryType2["AMULET"] = "amulet";
    AccessoryType2["MIRROR"] = "mirror";
    AccessoryType2["CROWN"] = "crown";
    return AccessoryType2;
  })(AccessoryType || {});
  var PotionType = /* @__PURE__ */ ((PotionType2) => {
    PotionType2["MANA_BREW"] = "mana_brew";
    PotionType2["CLARITY"] = "clarity";
    PotionType2["AMPLIFIER"] = "amplifier";
    PotionType2["VENOM"] = "venom";
    return PotionType2;
  })(PotionType || {});
  var SpellType = /* @__PURE__ */ ((SpellType3) => {
    SpellType3["HEAL"] = "heal";
    SpellType3["STRENGTH"] = "strength";
    SpellType3["SHIELD"] = "shield";
    SpellType3["WEAKEN"] = "weaken";
    SpellType3["CURSE"] = "curse";
    SpellType3["REFLECT"] = "reflect";
    SpellType3["INSPIRE"] = "inspire";
    SpellType3["DISPEL"] = "dispel";
    return SpellType3;
  })(SpellType || {});
  var KnightTitle = /* @__PURE__ */ ((KnightTitle2) => {
    KnightTitle2["SQUIRE"] = "Squire";
    KnightTitle2["KNIGHT"] = "Knight";
    KnightTitle2["CHAMPION"] = "Champion";
    KnightTitle2["PRINCE"] = "Prince";
    return KnightTitle2;
  })(KnightTitle || {});
  var LadyTitle = /* @__PURE__ */ ((LadyTitle2) => {
    LadyTitle2["MAIDEN"] = "Maiden";
    LadyTitle2["COURT_LADY"] = "Court Lady";
    LadyTitle2["FAVORITE"] = "Favorite";
    LadyTitle2["PRINCESS"] = "Princess";
    return LadyTitle2;
  })(LadyTitle || {});
  var NpcDifficulty = /* @__PURE__ */ ((NpcDifficulty2) => {
    NpcDifficulty2["SQUIRE"] = "squire";
    NpcDifficulty2["KNIGHT"] = "knight";
    NpcDifficulty2["CHAMPION"] = "champion";
    return NpcDifficulty2;
  })(NpcDifficulty || {});
  var ARMOR_STATS = {
    ["leather" /* LEATHER */]: { defense: 2, speedMod: 2 },
    ["chainmail" /* CHAINMAIL */]: { defense: 5, speedMod: 0 },
    ["plate" /* PLATE */]: { defense: 8, speedMod: -2 },
    ["fullplate" /* FULLPLATE */]: { defense: 12, speedMod: -4 }
  };
  var HORSE_STATS = {
    ["rouncy" /* ROUNCY */]: { speed: 1, powerMod: 3 },
    ["destrier" /* DESTRIER */]: { speed: 3, powerMod: 2 },
    ["courser" /* COURSER */]: { speed: 5, powerMod: 0 },
    ["palfrey" /* PALFREY */]: { speed: 7, powerMod: -2 }
  };
  var WEAPON_STATS = {
    ["sword" /* SWORD */]: { attack: 5, special: "none" },
    ["lance" /* LANCE */]: { attack: 7, special: "first_strike" },
    ["mace" /* MACE */]: { attack: 6, special: "pierce" },
    ["axe" /* AXE */]: { attack: 9, special: "slow" }
  };
  var MEDICINE_STATS = {
    ["herbs" /* HERBS */]: { hpBonus: 15 },
    ["tonic" /* TONIC */]: { hpBonus: 25 },
    ["potion" /* POTION */]: { hpBonus: 40 },
    ["elixir" /* ELIXIR */]: { hpBonus: 60 }
  };
  var DRESS_STATS = {
    ["royal" /* ROYAL */]: { atkWeight: 1.6, defWeight: 0.4, effect: "attack_focused" },
    ["noble" /* NOBLE */]: { atkWeight: 0.4, defWeight: 1.6, effect: "defense_focused" },
    ["mystic" /* MYSTIC */]: { atkWeight: 0.8, defWeight: 0.8, effect: "magic_bonus" },
    ["enchanted" /* ENCHANTED */]: { atkWeight: 0.9, defWeight: 0.9, effect: "spell_discount" }
  };
  var ACCESSORY_STATS = {
    ["wand" /* WAND */]: { spells: ["strength" /* STRENGTH */, "weaken" /* WEAKEN */] },
    ["amulet" /* AMULET */]: { spells: ["heal" /* HEAL */, "shield" /* SHIELD */] },
    ["mirror" /* MIRROR */]: { spells: ["curse" /* CURSE */, "reflect" /* REFLECT */] },
    ["crown" /* CROWN */]: { spells: ["inspire" /* INSPIRE */, "dispel" /* DISPEL */] }
  };
  var POTION_STATS = {
    ["mana_brew" /* MANA_BREW */]: { mpBonus: 30, spellCostDiscount: 0, buffDurationBonus: 0, debuffDurationBonus: 0 },
    ["clarity" /* CLARITY */]: { mpBonus: 0, spellCostDiscount: 5, buffDurationBonus: 0, debuffDurationBonus: 0 },
    ["amplifier" /* AMPLIFIER */]: { mpBonus: 0, spellCostDiscount: 0, buffDurationBonus: 1, debuffDurationBonus: 0 },
    ["venom" /* VENOM */]: { mpBonus: 0, spellCostDiscount: 0, buffDurationBonus: 0, debuffDurationBonus: 1 }
  };
  var SPELL_DEFINITIONS = {
    ["heal" /* HEAL */]: {
      type: "heal" /* HEAL */,
      name: "Healing",
      powerCost: 30,
      duration: 0,
      description: "Stamina +6%",
      targetsSelf: true
    },
    ["shield" /* SHIELD */]: {
      type: "shield" /* SHIELD */,
      name: "Shield",
      powerCost: 25,
      duration: 3,
      description: "Defense +8%",
      targetsSelf: true
    },
    ["strength" /* STRENGTH */]: {
      type: "strength" /* STRENGTH */,
      name: "Strength",
      powerCost: 25,
      duration: 3,
      description: "Attack +8%",
      targetsSelf: true
    },
    ["weaken" /* WEAKEN */]: {
      type: "weaken" /* WEAKEN */,
      name: "Weaken",
      powerCost: 35,
      duration: 3,
      description: "Enemy attack -8%",
      targetsSelf: false
    },
    ["curse" /* CURSE */]: {
      type: "curse" /* CURSE */,
      name: "Curse",
      powerCost: 35,
      duration: 3,
      description: "Enemy defense -8%",
      targetsSelf: false
    },
    ["reflect" /* REFLECT */]: {
      type: "reflect" /* REFLECT */,
      name: "Reflect",
      powerCost: 40,
      duration: 1,
      description: "Halves the damage he takes",
      targetsSelf: true
    },
    ["inspire" /* INSPIRE */]: {
      type: "inspire" /* INSPIRE */,
      name: "Inspire",
      powerCost: 40,
      duration: 2,
      description: "Attack +12%",
      targetsSelf: true
    },
    ["dispel" /* DISPEL */]: {
      type: "dispel" /* DISPEL */,
      name: "Dispel",
      powerCost: 30,
      duration: 0,
      description: "Removes the enemy's boosts",
      targetsSelf: false
    }
  };
  var KNIGHT_AVATAR_SKILLS = [
    { atk: 3, def: 3, sta: 3 },
    // k01 English   — Balanced
    { atk: 2, def: 5, sta: 2 },
    // k02 French    — Defensive
    { atk: 1, def: 5, sta: 3 },
    // k03 German    — Heavy
    { atk: 5, def: 1, sta: 3 },
    // k04 Moorish   — Aggressive
    { atk: 2, def: 3, sta: 4 },
    // k05 Byzantine — Resilient
    { atk: 5, def: 2, sta: 2 },
    // k06 Norse     — Berserker
    { atk: 3, def: 2, sta: 4 },
    // k07 Scottish  — Enduring
    { atk: 5, def: 3, sta: 1 }
    // k08 Saracen   — Swift
  ];
  var LADY_AVATAR_SKILLS = [
    { mag: 3, inf: 3, cha: 3 },
    // l01 English   — Balanced
    { mag: 1, inf: 5, cha: 3 },
    // l02 French    — Influential
    { mag: 3, inf: 2, cha: 4 },
    // l03 German    — Charismatic
    { mag: 5, inf: 1, cha: 3 },
    // l04 Moorish   — Arcane
    { mag: 2, inf: 5, cha: 2 },
    // l05 Byzantine — Diplomatic
    { mag: 2, inf: 3, cha: 4 },
    // l06 Norse     — Charming
    { mag: 3, inf: 1, cha: 5 },
    // l07 Scottish  — Radiant
    { mag: 5, inf: 3, cha: 1 }
    // l08 Persian   — Mystic
  ];
  function getKnightSkills(avatarUrl) {
    if (!avatarUrl) return KNIGHT_AVATAR_SKILLS[0];
    const m = avatarUrl.match(/k(\d+)\.png/i);
    if (!m) return KNIGHT_AVATAR_SKILLS[0];
    return KNIGHT_AVATAR_SKILLS[parseInt(m[1]) - 1] ?? KNIGHT_AVATAR_SKILLS[0];
  }
  function getLadySkills(avatarUrl) {
    if (!avatarUrl) return LADY_AVATAR_SKILLS[0];
    const m = avatarUrl.match(/l(\d+)\.png/i);
    if (!m) return LADY_AVATAR_SKILLS[0];
    return LADY_AVATAR_SKILLS[parseInt(m[1]) - 1] ?? LADY_AVATAR_SKILLS[0];
  }
  var POINTS = {
    WIN: 15,
    LOSS: -5,
    LADY_WIN_PRIORITY: [12, 6, 2],
    LADY_LOSS_PRIORITY: [-4, -1, 0],
    SPELL_BONUS: 2,
    ROYAL_DRESS_MULTIPLIER: 1.2,
    NOBLE_DRESS_LOSS_REDUCTION: 1
  };
  function getKnightTitle(points) {
    if (points >= 300) return "Prince" /* PRINCE */;
    if (points >= 151) return "Champion" /* CHAMPION */;
    if (points >= 51) return "Knight" /* KNIGHT */;
    return "Squire" /* SQUIRE */;
  }
  function getLadyTitle(points) {
    if (points >= 300) return "Princess" /* PRINCESS */;
    if (points >= 151) return "Favorite" /* FAVORITE */;
    if (points >= 51) return "Court Lady" /* COURT_LADY */;
    return "Maiden" /* MAIDEN */;
  }
  function computeKnightStats(eq) {
    const armor = ARMOR_STATS[eq.armor];
    const horse = HORSE_STATS[eq.horse];
    const weapon = WEAPON_STATS[eq.weapon];
    const medicine = MEDICINE_STATS[eq.medicine];
    return {
      maxHp: 100 + medicine.hpBonus,
      attack: 10 + weapon.attack + horse.powerMod,
      defense: 5 + armor.defense,
      speed: 5 + horse.speed + armor.speedMod
    };
  }

  // src/battle.ts
  var ANIM_TICKS = 5;
  var TICK_MS = 1500;

  // How a bout looks (the owner, 2026-10-29): both bars move, and the end is not visible from the first tick. The winner's and the loser's bars each follow one of a few scripts
  // (the share of their damage taken by tick 1..5, the last is always 1), picked at random for the bout and not by who wins, so the bar that falls fastest is not always the loser.
  var WINNER_SCRIPTS = [[0.65, 0.80, 0.90, 0.97, 1], [0.35, 0.60, 0.80, 0.92, 1], [0.50, 0.70, 0.85, 0.95, 1], [0.25, 0.50, 0.75, 0.90, 1]];
  var LOSER_SCRIPTS  = [[0.05, 0.10, 0.18, 0.30, 1], [0.15, 0.22, 0.32, 0.45, 1], [0.10, 0.20, 0.25, 0.40, 1], [0.20, 0.30, 0.45, 0.60, 1]];
  // Luck (the owner, 2026-10-29): after each lost bout in a row the human's fighter gets a little more chance to win: LUCK_STEP per loss, at most LUCK_MAX, the chance never lifted above
  // LUCK_CEILING; a win resets it. The tournament counts the losses (state.luckStreak) and tells the bout.
  var LUCK_STEP = 0.05;
  var LUCK_MAX = 0.20;
  var LUCK_CEILING = 0.85;
  var PRIORITY_WEIGHTS = [1, 0.6, 0.3];
  var SPELL_ATK_MOD = {
    ["strength" /* STRENGTH */]: 0.08,
    ["inspire" /* INSPIRE */]: 0.12,
    ["weaken" /* WEAKEN */]: -0.08
  };
  var SPELL_DEF_MOD = {
    ["shield" /* SHIELD */]: 0.08,
    ["curse" /* CURSE */]: -0.08
  };
  var SPELL_STA_MOD = {
    ["heal" /* HEAL */]: 0.06
  };
  function computeLadyMults(knightId, ladies) {
    let atkBonus = 0;
    let defBonus = 0;
    for (const lady of ladies) {
      const priIdx = lady.chosenKnights.indexOf(knightId);
      if (priIdx === -1) continue;
      const w = PRIORITY_WEIGHTS[priIdx];
      const s = getLadySkills(lady.avatarUrl);
      const d = DRESS_STATS[lady.outfit.dress];
      const base = (s.inf * 0.2 + lady.status / 100 * 0.3) * w;
      atkBonus += base * d.atkWeight;
      defBonus += base * d.defWeight;
    }
    return { atk: 1 + atkBonus, def: 1 + defBonus };
  }
  function computeAtkEff(k, ladyAtkMult, atkSpellMod) {
    const w = WEAPON_STATS[k.equipment.weapon];
    const h = HORSE_STATS[k.equipment.horse];
    const skills = getKnightSkills(k.avatarUrl);
    const base = (w.attack + h.powerMod) * k.weaponWornPct;
    return base * (1 + skills.atk * 0.2) * ladyAtkMult * (1 + atkSpellMod);
  }
  function computeDefEff(k, ladyDefMult, defSpellMod) {
    const a = ARMOR_STATS[k.equipment.armor];
    const skills = getKnightSkills(k.avatarUrl);
    return a.defense * k.armorWornPct * (1 + skills.def * 0.2) * ladyDefMult * (1 + defSpellMod);
  }
  function computeStamina(k, staSpellMod) {
    const skills = getKnightSkills(k.avatarUrl);
    return Math.max(0.1, k.healthWorn + staSpellMod) * (1 + skills.sta * 0.2);
  }
  var BattleEngine = class {
    constructor(knight1, knight2, ladies) {
      __publicField(this, "knight1", knight1);
      __publicField(this, "knight2", knight2);
      __publicField(this, "ladies", ladies);
      __publicField(this, "tick", 0);
      __publicField(this, "finished", false);
      // Spell accumulators: per-knight modifiers (fractional, e.g. 0.08 per cast)
      __publicField(this, "atkMod", {});
      __publicField(this, "defMod", {});
      __publicField(this, "staMod", {});
      __publicField(this, "pendingSpells", []);
      __publicField(this, "spellsCastByLady", /* @__PURE__ */ new Map());
      // Resolved outcome (computed at start)
      __publicField(this, "roll", Math.random());
      __publicField(this, "reflect", {});
      __publicField(this, "winner");
      __publicField(this, "loser");
      __publicField(this, "damageToWinner", 0);
      __publicField(this, "damageToLoser", 0);
      __publicField(this, "winProbability", 0);
      __publicField(this, "shownHp", [100, 100]);
      __publicField(this, "favour", null);
      __publicField(this, "scriptW", Math.floor(Math.random() * WINNER_SCRIPTS.length));
      __publicField(this, "scriptL", Math.floor(Math.random() * LOSER_SCRIPTS.length));
      // ─── public API ───────────────────────────────────────────────────────────
      // The single-player build has no timers: the caller steps the bout (every TICK_MS for a bout shown on
      // the screen, in a loop for one that is only computed).
      __publicField(this, "isK1Winner", false);
      __publicField(this, "winnerFinalHp", 0);
      __publicField(this, "loserFinalHp", 4);
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
    resolveOutcome() {
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
      this.winProbability = totalPower > 0 ? power1 * power1 / totalPower : 0.5;
      if (this.favour) {
        const mine1 = this.favour.id === this.knight1.id;
        const pm = mine1 ? this.winProbability : 1 - this.winProbability;
        const lifted = Math.max(pm, Math.min(LUCK_CEILING, pm + this.favour.bonus));
        this.winProbability = mine1 ? lifted : 1 - lifted;
      }
      const rolled = this.roll < this.winProbability;
      this.winner = rolled ? this.knight1 : this.knight2;
      this.loser = rolled ? this.knight2 : this.knight1;
      const k1Wins = this.winner.id === this.knight1.id;
      const atkW = k1Wins ? atk1 : atk2;
      const atkL = k1Wins ? atk2 : atk1;
      const defW = k1Wins ? def1 : def2;
      const defL = k1Wins ? def2 : def1;
      const pW = k1Wins ? this.winProbability : 1 - this.winProbability;
      this.damageToLoser = Math.max(0, atkW - defL) * Math.pow(0.5, this.reflect[this.loser.id] ?? 0);
      this.damageToWinner = Math.max(0, atkL - defW) * (1 - pW) * Math.pow(0.5, this.reflect[this.winner.id] ?? 0);
    }
    // luck: the human's fighter and how much more chance it has (the tournament knows)
    setFavour(id, bonus) {
      this.favour = bonus > 0 ? { id, bonus } : null;
    }
    // the chance that knight 1 wins as it stands now (the spells and the luck count): the screen shows it
    chance1() {
      return this.winProbability;
    }
    begin() {
      this.resolveOutcome();
      this.updateHp();
    }
    // the picture of the bout follows the outcome as it stands now
    updateHp() {
      this.winnerFinalHp = Math.round(100 - (30 + Math.min(1, this.damageToWinner / Math.max(this.damageToLoser, 1)) * 30));
      this.isK1Winner = this.winner.id === this.knight1.id;
    }
    step() {
      this.tick++;
      this.flushSpells();
      this.resolveOutcome();
      this.updateHp();
      const winnerHp = Math.round(100 - (100 - this.winnerFinalHp) * WINNER_SCRIPTS[this.scriptW][Math.min(this.tick, ANIM_TICKS) - 1]);
      const loserHp = Math.round(100 - (100 - this.loserFinalHp) * LOSER_SCRIPTS[this.scriptL][Math.min(this.tick, ANIM_TICKS) - 1]);
      // a bar never grows back (the winner can change while the spells are cast: the new winner goes on from where its bar is)
      const k1Hp = Math.min(this.isK1Winner ? winnerHp : loserHp, this.shownHp[0]);
      const k2Hp = Math.min(this.isK1Winner ? loserHp : winnerHp, this.shownHp[1]);
      this.shownHp = [k1Hp, k2Hp];
      const isFinished = this.tick >= ANIM_TICKS;
      const events = [];
      if (isFinished) {
        events.push({ tick: this.tick, type: "battle_end", message: `${this.winner.name} wins!`, actorId: this.winner.id, targetId: this.loser.id });
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
        loserId: this.loser.id
      };
    }
    result() {
      return {
        winnerId: this.winner.id,
        loserId: this.loser.id,
        damageToWinner: this.damageToWinner,
        damageToLoser: this.damageToLoser,
        spellsCastByLady: this.spellsCastByLady
      };
    }
    queueSpell(cast) {
      if (this.finished) return { ok: false, reason: "Battle is over" };
      const lady = this.ladies.find((l) => l.id === cast.ladyId);
      if (!lady) return { ok: false, reason: "Not your battle" };
      let discount = 0;
      if (lady.outfit.potion === "clarity" /* CLARITY */) discount += POTION_STATS["clarity" /* CLARITY */].spellCostDiscount;
      if (lady.outfit.dress === "enchanted" /* ENCHANTED */) discount += 5;
      const cost = Math.max(0, SPELL_DEFINITIONS[cast.spellType].powerCost - discount);
      if (lady.magicPower < cost) return { ok: false, reason: "Not enough magic power" };
      lady.magicPower -= cost;
      this.pendingSpells.push(cast);
      return { ok: true };
    }
    // ─── internals ────────────────────────────────────────────────────────────
    flushSpells() {
      for (const cast of this.pendingSpells) {
        const lady = this.ladies.find((l) => l.id === cast.ladyId);
        const ladySkills = getLadySkills(lady?.avatarUrl);
        const magScale = 1 + ladySkills.mag * 0.2;
        const atkM = (SPELL_ATK_MOD[cast.spellType] ?? 0) * magScale;
        const defM = (SPELL_DEF_MOD[cast.spellType] ?? 0) * magScale;
        const staM = (SPELL_STA_MOD[cast.spellType] ?? 0) * magScale;
        const tid = cast.targetKnightId;
        if (cast.spellType === "reflect" /* REFLECT */) this.reflect[tid] = (this.reflect[tid] ?? 0) + 1;
        if (cast.spellType === "dispel" /* DISPEL */) {
          for (const m of [this.atkMod, this.defMod, this.staMod]) if (m[tid] !== void 0) m[tid] = Math.min(0, m[tid]);
        }
        if (this.atkMod[tid] !== void 0) this.atkMod[tid] += atkM;
        if (this.defMod[tid] !== void 0) this.defMod[tid] += defM;
        if (this.staMod[tid] !== void 0) this.staMod[tid] += staM;
        const count = this.spellsCastByLady.get(cast.ladyId) ?? 0;
        this.spellsCastByLady.set(cast.ladyId, count + 1);
      }
      this.pendingSpells = [];
    }
  };

  // src/npc.ts
  var KNIGHT_NAMES = [
    "Sir William",
    "Sir Hugh",
    "Sir Geoffrey",
    "Sir Ranulf",
    "Sir Baldwyn",
    "Sir Osbert",
    "Sir Reginald",
    "Sir Eustace",
    "Sir Guillaume",
    "Sir Gautier",
    "Sir Bertrand",
    "Sir Renaud",
    "Sir Thibaut",
    "Sir Girard",
    "Sir Raoul",
    "Sir Godefroy",
    "Sir Arnaud",
    "Sir Joscelin",
    "Sir Anselm",
    "Sir Humphrey",
    "Sir Heinrich",
    "Sir Konrad",
    "Sir Friedrich",
    "Sir Gottfried",
    "Sir Ludwig",
    "Sir Dietrich",
    "Sir Burkhard",
    "Sir Hartmann",
    "Sir Wolfram",
    "Sir Ruprecht",
    "Sir Albrecht",
    "Sir Wigand"
  ];
  var LADY_NAMES = [
    "Lady Matilda",
    "Lady Eleanor",
    "Lady Cecily",
    "Lady Maud",
    "Lady Avice",
    "Lady Beatrice",
    "Lady Petronilla",
    "Lady Constance",
    "Lady Margery",
    "Lady Hawise",
    "Lady Alienor",
    "Lady Blanche",
    "Lady Marguerite",
    "Lady Isabeau",
    "Lady Alix",
    "Lady Heloise",
    "Lady Ermengarde",
    "Lady Adele",
    "Lady Clemence",
    "Lady Philippa",
    "Lady Hildegard",
    "Lady Mechthild",
    "Lady Agnes",
    "Lady Sophia",
    "Lady Elisabeth",
    "Lady Kunigunde",
    "Lady Hedwig",
    "Lady Gertrude",
    "Lady Richenza",
    "Lady Gisela",
    "Lady Adelheid",
    "Lady Sybilla"
  ];
  var KNIGHT_PROFILES = [
    { armor: "leather" /* LEATHER */, horse: "palfrey" /* PALFREY */, weapon: "sword" /* SWORD */, medicine: "herbs" /* HERBS */ },
    { armor: "leather" /* LEATHER */, horse: "palfrey" /* PALFREY */, weapon: "lance" /* LANCE */, medicine: "herbs" /* HERBS */ },
    { armor: "leather" /* LEATHER */, horse: "courser" /* COURSER */, weapon: "sword" /* SWORD */, medicine: "tonic" /* TONIC */ },
    { armor: "leather" /* LEATHER */, horse: "courser" /* COURSER */, weapon: "axe" /* AXE */, medicine: "herbs" /* HERBS */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "palfrey" /* PALFREY */, weapon: "sword" /* SWORD */, medicine: "tonic" /* TONIC */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "courser" /* COURSER */, weapon: "lance" /* LANCE */, medicine: "tonic" /* TONIC */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "courser" /* COURSER */, weapon: "mace" /* MACE */, medicine: "herbs" /* HERBS */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "courser" /* COURSER */, weapon: "axe" /* AXE */, medicine: "tonic" /* TONIC */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "destrier" /* DESTRIER */, weapon: "sword" /* SWORD */, medicine: "tonic" /* TONIC */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "destrier" /* DESTRIER */, weapon: "lance" /* LANCE */, medicine: "potion" /* POTION */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "palfrey" /* PALFREY */, weapon: "mace" /* MACE */, medicine: "potion" /* POTION */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "rouncy" /* ROUNCY */, weapon: "lance" /* LANCE */, medicine: "tonic" /* TONIC */ },
    { armor: "plate" /* PLATE */, horse: "courser" /* COURSER */, weapon: "sword" /* SWORD */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "courser" /* COURSER */, weapon: "lance" /* LANCE */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "courser" /* COURSER */, weapon: "mace" /* MACE */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "destrier" /* DESTRIER */, weapon: "sword" /* SWORD */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "destrier" /* DESTRIER */, weapon: "lance" /* LANCE */, medicine: "elixir" /* ELIXIR */ },
    { armor: "plate" /* PLATE */, horse: "destrier" /* DESTRIER */, weapon: "mace" /* MACE */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "rouncy" /* ROUNCY */, weapon: "axe" /* AXE */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "palfrey" /* PALFREY */, weapon: "sword" /* SWORD */, medicine: "elixir" /* ELIXIR */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "rouncy" /* ROUNCY */, weapon: "mace" /* MACE */, medicine: "elixir" /* ELIXIR */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "rouncy" /* ROUNCY */, weapon: "axe" /* AXE */, medicine: "elixir" /* ELIXIR */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "destrier" /* DESTRIER */, weapon: "lance" /* LANCE */, medicine: "elixir" /* ELIXIR */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "destrier" /* DESTRIER */, weapon: "mace" /* MACE */, medicine: "potion" /* POTION */ },
    { armor: "leather" /* LEATHER */, horse: "destrier" /* DESTRIER */, weapon: "lance" /* LANCE */, medicine: "potion" /* POTION */ },
    { armor: "leather" /* LEATHER */, horse: "rouncy" /* ROUNCY */, weapon: "mace" /* MACE */, medicine: "tonic" /* TONIC */ },
    { armor: "plate" /* PLATE */, horse: "palfrey" /* PALFREY */, weapon: "axe" /* AXE */, medicine: "tonic" /* TONIC */ },
    { armor: "chainmail" /* CHAINMAIL */, horse: "rouncy" /* ROUNCY */, weapon: "axe" /* AXE */, medicine: "potion" /* POTION */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "courser" /* COURSER */, weapon: "sword" /* SWORD */, medicine: "tonic" /* TONIC */ },
    { armor: "leather" /* LEATHER */, horse: "rouncy" /* ROUNCY */, weapon: "axe" /* AXE */, medicine: "elixir" /* ELIXIR */ },
    { armor: "fullplate" /* FULLPLATE */, horse: "palfrey" /* PALFREY */, weapon: "axe" /* AXE */, medicine: "potion" /* POTION */ },
    { armor: "plate" /* PLATE */, horse: "rouncy" /* ROUNCY */, weapon: "sword" /* SWORD */, medicine: "elixir" /* ELIXIR */ }
  ];
  var LADY_PROFILES = [
    { dress: "noble" /* NOBLE */, accessory: "wand" /* WAND */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
    { dress: "royal" /* ROYAL */, accessory: "wand" /* WAND */, potion: "amplifier" /* AMPLIFIER */, medicine: "tonic" /* TONIC */ },
    { dress: "mystic" /* MYSTIC */, accessory: "wand" /* WAND */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "wand" /* WAND */, potion: "clarity" /* CLARITY */, medicine: "potion" /* POTION */ },
    { dress: "noble" /* NOBLE */, accessory: "amulet" /* AMULET */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
    { dress: "royal" /* ROYAL */, accessory: "amulet" /* AMULET */, potion: "mana_brew" /* MANA_BREW */, medicine: "tonic" /* TONIC */ },
    { dress: "mystic" /* MYSTIC */, accessory: "amulet" /* AMULET */, potion: "mana_brew" /* MANA_BREW */, medicine: "potion" /* POTION */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "amulet" /* AMULET */, potion: "clarity" /* CLARITY */, medicine: "herbs" /* HERBS */ },
    { dress: "noble" /* NOBLE */, accessory: "amulet" /* AMULET */, potion: "amplifier" /* AMPLIFIER */, medicine: "tonic" /* TONIC */ },
    { dress: "royal" /* ROYAL */, accessory: "amulet" /* AMULET */, potion: "clarity" /* CLARITY */, medicine: "elixir" /* ELIXIR */ },
    { dress: "noble" /* NOBLE */, accessory: "mirror" /* MIRROR */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
    { dress: "royal" /* ROYAL */, accessory: "mirror" /* MIRROR */, potion: "venom" /* VENOM */, medicine: "tonic" /* TONIC */ },
    { dress: "mystic" /* MYSTIC */, accessory: "mirror" /* MIRROR */, potion: "mana_brew" /* MANA_BREW */, medicine: "potion" /* POTION */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "mirror" /* MIRROR */, potion: "clarity" /* CLARITY */, medicine: "herbs" /* HERBS */ },
    { dress: "noble" /* NOBLE */, accessory: "mirror" /* MIRROR */, potion: "venom" /* VENOM */, medicine: "elixir" /* ELIXIR */ },
    { dress: "royal" /* ROYAL */, accessory: "mirror" /* MIRROR */, potion: "amplifier" /* AMPLIFIER */, medicine: "potion" /* POTION */ },
    { dress: "noble" /* NOBLE */, accessory: "crown" /* CROWN */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
    { dress: "royal" /* ROYAL */, accessory: "crown" /* CROWN */, potion: "amplifier" /* AMPLIFIER */, medicine: "tonic" /* TONIC */ },
    { dress: "mystic" /* MYSTIC */, accessory: "crown" /* CROWN */, potion: "mana_brew" /* MANA_BREW */, medicine: "potion" /* POTION */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "crown" /* CROWN */, potion: "clarity" /* CLARITY */, medicine: "herbs" /* HERBS */ },
    { dress: "noble" /* NOBLE */, accessory: "crown" /* CROWN */, potion: "venom" /* VENOM */, medicine: "tonic" /* TONIC */ },
    { dress: "royal" /* ROYAL */, accessory: "crown" /* CROWN */, potion: "amplifier" /* AMPLIFIER */, medicine: "elixir" /* ELIXIR */ },
    { dress: "mystic" /* MYSTIC */, accessory: "crown" /* CROWN */, potion: "clarity" /* CLARITY */, medicine: "potion" /* POTION */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "amulet" /* AMULET */, potion: "amplifier" /* AMPLIFIER */, medicine: "tonic" /* TONIC */ },
    { dress: "mystic" /* MYSTIC */, accessory: "wand" /* WAND */, potion: "venom" /* VENOM */, medicine: "potion" /* POTION */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "mirror" /* MIRROR */, potion: "mana_brew" /* MANA_BREW */, medicine: "elixir" /* ELIXIR */ },
    { dress: "royal" /* ROYAL */, accessory: "wand" /* WAND */, potion: "venom" /* VENOM */, medicine: "potion" /* POTION */ },
    { dress: "mystic" /* MYSTIC */, accessory: "amulet" /* AMULET */, potion: "clarity" /* CLARITY */, medicine: "elixir" /* ELIXIR */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "crown" /* CROWN */, potion: "venom" /* VENOM */, medicine: "potion" /* POTION */ },
    { dress: "royal" /* ROYAL */, accessory: "mirror" /* MIRROR */, potion: "clarity" /* CLARITY */, medicine: "elixir" /* ELIXIR */ },
    { dress: "mystic" /* MYSTIC */, accessory: "crown" /* CROWN */, potion: "venom" /* VENOM */, medicine: "elixir" /* ELIXIR */ },
    { dress: "enchanted" /* ENCHANTED */, accessory: "wand" /* WAND */, potion: "amplifier" /* AMPLIFIER */, medicine: "potion" /* POTION */ }
  ];
  var NPC_KNIGHT_ROSTER = KNIGHT_PROFILES.map((equipment, i) => ({
    name: KNIGHT_NAMES[i],
    equipment,
    baseStats: computeKnightStats(equipment),
    avatarUrl: `/assets/avatar/k${String(i + 1).padStart(2, "0")}.png`
  }));
  var NPC_LADY_ROSTER = LADY_PROFILES.map((outfit, i) => {
    let mp = 100;
    if (outfit.dress === "mystic" /* MYSTIC */) mp += 20;
    mp += POTION_STATS[outfit.potion].mpBonus;
    return {
      name: LADY_NAMES[i],
      outfit,
      magicPower: mp,
      maxMagicPower: mp,
      avatarUrl: `/assets/avatar/l${String(i + 1).padStart(2, "0")}.png`
    };
  });
  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }
  function npcChoosePartners(pool, _difficulty) {
    const shuffled = [...pool].sort(() => Math.random() - 0.5).slice(0, 3);
    return [shuffled[0]?.id ?? null, shuffled[1]?.id ?? null, shuffled[2]?.id ?? null];
  }
  function npcDecideSpell(lady, ownKnightHpRatio, enemyKnightHpRatio, difficulty) {
    const spells = ACCESSORY_STATS[lady.outfit.accessory].spells;
    if (spells.length === 0) return null;
    if (difficulty === "squire" /* SQUIRE */) return Math.random() < 0.33 ? pick(spells) : null;
    if (difficulty === "knight" /* KNIGHT */) {
      if (ownKnightHpRatio < 0.4 && spells.includes("heal" /* HEAL */)) return "heal" /* HEAL */;
      return Math.random() < 0.5 ? pick(spells) : null;
    }
    if (ownKnightHpRatio < 0.35 && spells.includes("heal" /* HEAL */)) return "heal" /* HEAL */;
    if (ownKnightHpRatio < 0.5 && spells.includes("shield" /* SHIELD */)) return "shield" /* SHIELD */;
    if (enemyKnightHpRatio > 0.7 && spells.includes("weaken" /* WEAKEN */)) return "weaken" /* WEAKEN */;
    if (spells.includes("strength" /* STRENGTH */)) return "strength" /* STRENGTH */;
    return pick(spells);
  }

  // src/tournament.ts
  var TOTAL_KNIGHTS = 8;
  var TOTAL_LADIES = 8;
  var TOTAL_ROUNDS = 7;
  var K_DEF = 0.1;
  var K_ATK = 0.08;
  var K_HEALTH = 0.06;
  var HEALTH_FLOOR = 0.1;
  var LADY_PRIORITY_WEIGHTS = [1, 0.6, 0.3];
  var STATUS_BASE_GAIN = 10;
  var STATUS_BASE_LOSS = 7;
  var Tournament = class _Tournament {
    // the bouts of the round in progress
    constructor(state, me, log) {
      __publicField(this, "state", state);
      __publicField(this, "me", me);
      __publicField(this, "log", []);
      if (log) this.log = log;
    }
    // ─── creation ───────────────────────────────────────────────────────────────
    static create(role, avatarNo, name) {
      const n = String(avatarNo).padStart(2, "0");
      const state = {
        roomCode: "SOLO",
        hostId: "me",
        phase: "prepare",
        round: 1,
        totalRounds: TOTAL_ROUNDS,
        knights: [],
        ladies: [],
        schedule: [],
        currentBattleIndex: 0,
        currentBattle: null,
        prepareDeadline: null,
        nextBoutAt: null
      };
      for (let i = 0; i < TOTAL_KNIGHTS; i++) {
        const t2 = NPC_KNIGHT_ROSTER[i];
        state.knights.push({
          id: `npc_k${i + 1}`,
          name: t2.name,
          isNPC: true,
          npcDifficulty: "squire" /* SQUIRE */,
          role: "knight",
          points: 0,
          isConnected: true,
          lobbyReady: true,
          equipment: { ...t2.equipment },
          baseStats: { ...t2.baseStats },
          chosenLadies: [null, null, null],
          title: "Squire" /* SQUIRE */,
          prepareReady: true,
          avatarUrl: t2.avatarUrl,
          healthWorn: 1,
          armorWornPct: 1,
          weaponWornPct: 1,
          capturedArmors: [],
          capturedHorses: [],
          wins: 0,
          bouts: 0
        });
      }
      for (let i = 0; i < TOTAL_LADIES; i++) {
        const t2 = NPC_LADY_ROSTER[i];
        state.ladies.push({
          id: `npc_l${i + 1}`,
          name: t2.name,
          isNPC: true,
          npcDifficulty: "squire" /* SQUIRE */,
          role: "lady",
          points: 0,
          isConnected: true,
          lobbyReady: true,
          outfit: { ...t2.outfit },
          magicPower: t2.magicPower,
          maxMagicPower: t2.maxMagicPower,
          chosenKnights: [null, null, null],
          title: "Maiden" /* MAIDEN */,
          prepareReady: true,
          avatarUrl: t2.avatarUrl,
          status: 0
        });
      }
      const url = `assets/avatar/${role === "knight" ? "k" : "l"}${n}.png`;
      if (role === "knight") {
        const eq = { armor: "chainmail" /* CHAINMAIL */, horse: "courser" /* COURSER */, weapon: "sword" /* SWORD */, medicine: "herbs" /* HERBS */ };
        const idx = state.knights.findIndex((k) => k.avatarUrl && k.avatarUrl.endsWith(`k${n}.png`));
        const me = {
          id: "me",
          name,
          isNPC: false,
          role: "knight",
          points: 0,
          isConnected: true,
          lobbyReady: true,
          equipment: eq,
          baseStats: computeKnightStats(eq),
          chosenLadies: [null, null, null],
          title: "Squire" /* SQUIRE */,
          prepareReady: false,
          avatarUrl: url,
          healthWorn: 1,
          armorWornPct: 1,
          weaponWornPct: 1,
          capturedArmors: [],
          capturedHorses: [],
          wins: 0,
          bouts: 0
        };
        state.knights[idx >= 0 ? idx : 0] = me;
      } else {
        const idx = state.ladies.findIndex((l) => l.avatarUrl && l.avatarUrl.endsWith(`l${n}.png`));
        const me = {
          id: "me",
          name,
          isNPC: false,
          role: "lady",
          points: 0,
          isConnected: true,
          lobbyReady: true,
          outfit: { dress: "noble" /* NOBLE */, accessory: "amulet" /* AMULET */, potion: "mana_brew" /* MANA_BREW */, medicine: "herbs" /* HERBS */ },
          magicPower: 130,
          maxMagicPower: 130,
          chosenKnights: [null, null, null],
          title: "Maiden" /* MAIDEN */,
          prepareReady: false,
          avatarUrl: url,
          status: 0
        };
        state.ladies[idx >= 0 ? idx : 0] = me;
      }
      const t = new _Tournament(state, { id: "me", role });
      t.buildSchedule();
      t.beginPrepare();
      return t;
    }
    get meK() {
      return this.state.knights.find((k) => k.id === this.me.id);
    }
    get meL() {
      return this.state.ladies.find((l) => l.id === this.me.id);
    }
    get player() {
      return this.meK ?? this.meL;
    }
    knight(id) {
      return this.state.knights.find((k) => k.id === id);
    }
    lady(id) {
      return this.state.ladies.find((l) => l.id === id);
    }
    // Berger round-robin: every pair meets once over seven rounds
    buildSchedule() {
      const ids = this.state.knights.map((k) => k.id), N = ids.length;
      const rot = ids.slice(0, N - 1), fixed = ids[N - 1], pairs = [];
      let seq = 0;
      for (let round = 1; round <= TOTAL_ROUNDS; round++) {
        pairs.push({ id: `p${++seq}`, round, knight1Id: fixed, knight2Id: rot[0], completed: false });
        for (let k = 1; k < N / 2; k++) pairs.push({ id: `p${++seq}`, round, knight1Id: rot[k], knight2Id: rot[N - 1 - k], completed: false });
        rot.unshift(rot.pop());
      }
      this.state.schedule = pairs;
    }
    // ─── prepare phase ──────────────────────────────────────────────────────────
    beginPrepare() {
      this.state.phase = "prepare";
      this.state.currentBattle = null;
      this.log = [];
      for (const p of [...this.state.knights, ...this.state.ladies]) p.prepareReady = p.isNPC;
      for (const k of this.state.knights) k.chosenLadies = [null, null, null];
      for (const l of this.state.ladies) {
        l.chosenKnights = [null, null, null];
        l.magicPower = l.maxMagicPower;
      }
      for (const k of this.state.knights) {
        const bonus = MEDICINE_STATS[k.equipment.medicine].hpBonus / 100;
        k.healthWorn = Math.min(1, k.healthWorn + bonus);
      }
      for (const k of this.state.knights) if (k.isNPC) k.chosenLadies = npcChoosePartners(this.state.ladies, k.npcDifficulty ?? "squire" /* SQUIRE */);
      for (const l of this.state.ladies) if (l.isNPC) l.chosenKnights = npcChoosePartners(this.state.knights, l.npcDifficulty ?? "squire" /* SQUIRE */);
    }
    setEquipment(equipment) {
      const knight = this.meK;
      if (!knight) return;
      if (equipment.armor !== knight.equipment.armor) {
        const captured = knight.capturedArmors.find((a) => a.type === equipment.armor);
        if (captured) {
          knight.armorWornPct = captured.wornPct;
          knight.capturedArmors.splice(knight.capturedArmors.indexOf(captured), 1);
        } else knight.armorWornPct = 1;
      }
      if (equipment.weapon !== knight.equipment.weapon) knight.weaponWornPct = 1;
      knight.equipment = equipment;
      knight.baseStats = computeKnightStats(equipment);
    }
    setOutfit(outfit) {
      const lady = this.meL;
      if (!lady) return;
      lady.outfit = outfit;
      let max = 100;
      if (outfit.dress === "mystic" /* MYSTIC */) max += 20;
      max += POTION_STATS[outfit.potion].mpBonus;
      lady.maxMagicPower = max;
      lady.magicPower = max;
    }
    setPartners(ids) {
      const k = this.meK, l = this.meL;
      if (k) k.chosenLadies = ids;
      else if (l) l.chosenKnights = ids;
    }
    // ─── bouts ──────────────────────────────────────────────────────────────────
    roundPairs() {
      return this.state.schedule.filter((p) => p.round === this.state.round);
    }
    nextPair() {
      return this.roundPairs().find((p) => !p.completed);
    }
    // the fighter of this bout that is the human's (their knight, or the first knight their lady backs), or null
    mineIn(pair) {
      if (this.me.role === "knight") return pair.knight1Id === this.me.id || pair.knight2Id === this.me.id ? this.me.id : null;
      return this.meL.chosenKnights.find((id) => id === pair.knight1Id || id === pair.knight2Id) ?? null;
    }
    // luck: after lost bouts in a row the human's fighter gets a little more chance (LUCK_STEP each, at most LUCK_MAX)
    favourFor(pair) {
      const id = this.mineIn(pair), n = this.state.luckStreak || 0;
      return id && n > 0 ? { id, bonus: Math.min(LUCK_MAX, LUCK_STEP * n) } : null;
    }
    // does the human have a say in this bout (their knight fights, or their lady backs a fighter)?
    isLive(pair) {
      if (this.me.role === "knight") return pair.knight1Id === this.me.id || pair.knight2Id === this.me.id;
      const l = this.meL;
      return l.chosenKnights.includes(pair.knight1Id) || l.chosenKnights.includes(pair.knight2Id);
    }
    makeBout(pair) {
      const k1 = this.knight(pair.knight1Id), k2 = this.knight(pair.knight2Id);
      const ladies = this.state.ladies.filter((l) => l.chosenKnights.includes(k1.id) || l.chosenKnights.includes(k2.id));
      const engine = new BattleEngine(k1, k2, ladies);
      const fav = this.favourFor(pair);
      if (fav) engine.setFavour(fav.id, fav.bonus);
      engine.begin();
      const mp = {};
      for (const l of ladies) mp[l.id] = l.magicPower;
      this.state.phase = "battle";
      return { pair, k1, k2, ladies, engine, tick: null, mp, log: [] };
    }
    // one step of a bout; the NPC ladies cast after the tick, like the server did
    stepBout(b) {
      const tick = b.engine.step();
      b.tick = tick;
      if (!tick.finished) this.npcCast(b, tick);
      return tick;
    }
    // a bout the human does not see: the same steps, in a loop
    quickBout(pair) {
      const b = this.makeBout(pair);
      while (!this.stepBout(b).finished) {
      }
      return this.finishBout(b);
    }
    finishBout(b) {
      const result = b.engine.result();
      const before = this.pointsOfMe();
      b.pair.completed = true;
      b.pair.winnerId = result.winnerId;
      b.pair.loserId = result.loserId;
      this.applyBattleResult(result);
      const after = this.pointsOfMe();
      const involved = this.isLive(b.pair);
      const rec = {
        round: this.state.round,
        winnerId: result.winnerId,
        loserId: result.loserId,
        winnerName: this.knight(result.winnerId).name,
        loserName: this.knight(result.loserId).name,
        mineDelta: involved ? Math.round(after - before) : null
      };
      this.log.push(rec);
      if (involved) this.state.luckStreak = result.winnerId === this.mineIn(b.pair) ? 0 : (this.state.luckStreak || 0) + 1;
      this.state.phase = "standings";
      return rec;
    }
    pointsOfMe() {
      return this.player.points;
    }
    castSpell(b, spellType, targetKnightId) {
      const lady = this.meL;
      if (!lady) return { ok: false, reason: "Only a lady casts spells" };
      const r = b.engine.queueSpell({ ladyId: lady.id, spellType, targetKnightId });
      if (r.ok) b.log.push(`You cast ${SPELL_DEFINITIONS[spellType].name} on ${this.knight(targetKnightId).name}`);
      return r;
    }
    // a bout that was open when the app closed starts again: the spent magic comes back
    abandonBout(b) {
      for (const l of b.ladies) l.magicPower = b.mp[l.id] ?? l.magicPower;
      this.state.phase = "prepare";
    }
    npcCast(b, tick) {
      for (const lady of b.ladies) {
        if (!lady.isNPC) continue;
        const ownId = lady.chosenKnights[0] ?? lady.chosenKnights[1] ?? lady.chosenKnights[2];
        if (!ownId) continue;
        const own = ownId === b.k1.id ? b.k1 : b.k2, enemy = ownId === b.k1.id ? b.k2 : b.k1;
        const ownHp = ownId === b.k1.id ? tick.knight1.currentHp : tick.knight2.currentHp;
        const enemyHp = ownId === b.k1.id ? tick.knight2.currentHp : tick.knight1.currentHp;
        const spell = npcDecideSpell(lady, ownHp / own.baseStats.maxHp, enemyHp / enemy.baseStats.maxHp, lady.npcDifficulty ?? "squire" /* SQUIRE */);
        if (!spell) continue;
        const def = SPELL_DEFINITIONS[spell];
        if (!ACCESSORY_STATS[lady.outfit.accessory].spells.includes(spell)) continue;
        if (lady.magicPower < def.powerCost) continue;
        const target = def.targetsSelf ? own : enemy;
        if (b.engine.queueSpell({ ladyId: lady.id, spellType: spell, targetKnightId: target.id }).ok) b.log.push(`${lady.name} casts ${def.name} on ${target.name}`);
      }
    }
    // ─── scoring (the server's rules; the hidden +2 it gave humans is not here) ─
    applyBattleResult(result) {
      const winner = this.knight(result.winnerId), loser = this.knight(result.loserId);
      const toWinner = Number.isFinite(result.damageToWinner) ? result.damageToWinner : 0;
      const toLoser = Number.isFinite(result.damageToLoser) ? result.damageToLoser : 0;
      winner.points += POINTS.WIN + Math.round(toLoser - toWinner);
      loser.points += Math.round(toWinner - toLoser);
      winner.wins++;
      winner.bouts++;
      loser.bouts++;
      winner.title = getKnightTitle(winner.points);
      loser.title = getKnightTitle(loser.points);
      winner.armorWornPct = Math.max(0, winner.armorWornPct - toWinner * K_DEF);
      winner.weaponWornPct = Math.max(0, winner.weaponWornPct - toWinner * K_ATK);
      loser.armorWornPct = Math.max(0, loser.armorWornPct - toLoser * K_DEF);
      loser.weaponWornPct = Math.max(0, loser.weaponWornPct - toLoser * K_ATK);
      loser.healthWorn = Math.max(HEALTH_FLOOR, loser.healthWorn - toLoser * K_HEALTH);
      if (loser.equipment.armor !== "leather" /* LEATHER */) {
        winner.capturedArmors.push({ type: loser.equipment.armor, wornPct: loser.armorWornPct });
        const spare = loser.capturedArmors.shift();
        if (spare) {
          loser.equipment = { ...loser.equipment, armor: spare.type };
          loser.armorWornPct = spare.wornPct;
        } else {
          loser.equipment = { ...loser.equipment, armor: "leather" /* LEATHER */ };
          loser.armorWornPct = 0.5;
        }
      }
      if (loser.equipment.horse !== "rouncy" /* ROUNCY */) {
        winner.capturedHorses.push({ type: loser.equipment.horse });
        const spare = loser.capturedHorses.shift();
        loser.equipment = { ...loser.equipment, horse: spare ? spare.type : "rouncy" /* ROUNCY */ };
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
          if (id === result.winnerId) lady.status = Math.min(100, lady.status + STATUS_BASE_GAIN * (1 + s.cha * 0.2) * w);
          else if (id === result.loserId) lady.status = Math.max(0, lady.status - STATUS_BASE_LOSS * w);
        }
      }
    }
    // ─── rounds ─────────────────────────────────────────────────────────────────
    // all bouts of the round are over: the next round, or the end
    endRound() {
      if (this.state.round >= TOTAL_ROUNDS) {
        this.state.phase = "finished";
        return;
      }
      this.state.round++;
      this.beginPrepare();
    }
    knightsRanked() {
      return [...this.state.knights].sort((a, b) => b.points - a.points || b.wins - a.wins);
    }
    ladiesRanked() {
      return [...this.state.ladies].sort((a, b) => b.points - a.points);
    }
    save() {
      return { state: this.state, me: this.me, log: this.log };
    }
    static restore(s) {
      return new _Tournament(s.state, s.me, s.log);
    }
  };
  return __toCommonJS(tournament_exports);
})();
