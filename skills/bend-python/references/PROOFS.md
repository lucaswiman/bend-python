# Laws and proofs

## Contents

1. Files and the build gate
2. Stating laws
3. Proof mechanics
4. Recipes
5. Writing code that can be proved
6. Mutation checking
7. What a proof does and does not cover

## 1. Files and the build gate

- `LAWS.bend` imports the code and states laws. The user owns these statements.
- `PROOF.bend` imports `LAWS.bend` (as `Laws`) and the code, and proves law
  `name` with `def Laws.name(args):`. Bend refuses a PROOF.bend beside a
  LAWS.bend it does not import.
- `bend PROOF.bend` must print `All terms check` (possibly followed by a list
  of defs that rely on unsafe code). An open law or `?hole` gives
  `N TODOs found`.
- With `BendExtension("pkg._core", "src/pkg/core.bend", proofs=["PROOF.bend"])`
  every build runs the check and stops with
  `error: Bend proof check failed: PROOF.bend` on failure. The SDK's own laws
  (in the vendored `bend/`) are checked too.
- Keep unproved-but-approved claims in a file that does not gate the build
  (e.g. `PENDING.bend`) and test that it reports exactly one TODO per law, so
  the statements keep type-checking as the code changes.

## 2. Stating laws

```bend
law name:
  for x: T                 # universally quantified (also for +x, for -x)
  for h: {f(x) == True{} : Bool}    # a hypothesis
  {g(x) == x : T}          # the claim
```

- A law's claim is a type, usually an equality `{a == b : T}`;
  `{a != b : T}` is `{a == b : T} -> Empty`.
- Binders used more than once in the proof need `for +x`.
- Hypotheses are stated as `{predicate(x) == True{} : Bool}` with the
  predicate written as an ordinary Bool-valued def (a *validator*). This keeps
  the law readable and lets tests call the same validator at runtime.
- Prefer statements about the functions users actually call. Laws that only
  restate how a function is built (definitional laws, proved by `{==}`) are
  still valuable as a specification (for example, "d/dx sin(u) = cos(u)·u'")
  but say so when presenting them.
- Exact-equality laws about normalized or simplified output are often false
  even when the math is true, because two routes produce different normal
  forms. Search for counterexamples before proposing them.

## 3. Proof mechanics

- A proof is a def whose return type is the claim. `{==}` proves an equality
  when both sides compute to the same term.
- `match x:` in a proof refines the goal **and the hypotheses** mentioning `x`.
  Only constructor patterns refine: in `case _:` the value stays unknown, so a
  function that matches on it stays stuck. Spell out every constructor
  (`case LT{}:` and `case EQ{}:` separately) when the goal depends on it.
- A recursive call on a smaller argument is the induction hypothesis.
- **Rewriting**: `%e : P` with `e : {a == b : T}` requires the current goal to
  be `P` with **`b`** at every `_`, and continues with `P` with `a` there.
  **Rule of thumb: the term you want to get rid of must be on the right of
  `e`.** A hypothesis or lemma usually has it on the left (`{f(x) == 0}`), so
  flip it first with `Equal.sym`, or state your own lemmas the other way round
  (`{0 == f(x)}`).
  So to replace a complicated subterm `c` by a simple `s`, you need
  `e : {s == c}`. State helper lemmas as `{simple == complicated}`, or flip
  with `Equal.sym(T, a, b, e)`:

  ```bend
  # goal: {Word.cmp.fin(ab, bb, Word.cmp(p, at, bt)) == EQ{} : Cmp}
  # IH:   {Word.cmp(p, at, bt) == EQ{} : Cmp}
  %Equal.sym(Cmp, Word.cmp(p, at, bt), EQ{}, IH) : {Word.cmp.fin(ab, bb, _) == EQ{} : Cmp}
  ```
  Write `P` in full; it only has to be convertible to the goal (definitions
  unfold), so you may write the unreduced form.
- Several rewrites in a row are fine; `{==}` (or a lemma) closes the goal.
- `?name` prints the current goal and context. Use it constantly.
- Helper lemmas are ordinary typed defs (not in the `Laws.` namespace):
  `def u32_eq(x: U32) -> {EQ{} == U32.cmp(x, x) : Cmp}: ...`
- Look for lemmas Base already proves before writing your own:
  `bend base | grep '^law'` lists them (`Equal.sym`, `Equal.trans`,
  `Equal.cong(A, B, f, a, b, e)`, `Nat.ge_refl`, `Nat.max_ge_l`,
  `Nat.max_ge_r`, `U32.add_comm`, `Word.add_comm`, ...). Nat has more proved
  facts than U32; for counters and bounds that need proofs, Nat is often easier.
  Anything else about U32 you prove over `Word(n)`.
- `_` is only a motive placeholder. Erased arguments in ordinary calls must be
  written out: `false_ne_true({a == b : T}, h)`, not `false_ne_true(_, h)`.

## 4. Recipes

**Absurd hypotheses.** From `h : {LT{} == EQ{} : Cmp}` prove anything by
rewriting through a type-valued discriminator:

```bend
def Cmp.at_eq(c: Cmp, G: Type) -> Type:
  match c:
    case EQ{}:
      G
    case _:
      Unit

def lt_ne_eq(-G: Type, h: {LT{} == EQ{} : Cmp}) -> G:
  %h : Cmp.at_eq(_, G)
  Unit{}
```

The same shape handles `{False{} == True{} : Bool}` (discriminate on `True`).

**Bool hypotheses.** Validators return `Bool.and(...)` trees; peel them:

```bend
def and_l(a: Bool, b: Bool, h: {Bool.and(a, b) == True{} : Bool}) -> {a == True{} : Bool}:
  match a:
    case True{}:
      {==}
    case False{}:
      false_ne_true({False{} == True{} : Bool}, h)
```

(`Bool.and` matches its first argument, so `and_r` returns `h` directly in the
`True` case.) Turn `{Bool.not(b) == True{}}` into `{False{} == b}` by matching
`b`, ready to rewrite `b`.

**Chaining equalities instead of rewriting.** `Equal.trans(T, a, b, c, ab,
bc)` and `Equal.cong(A, B, f, a, b, e)` (`bend base Equal` prints them) take
their endpoints explicitly, and a proof whose type is only *convertible* to
`{a == b}` is accepted. Chains of lemmas are often easier to get right this way
than with a series of `%e : P` motives:

```bend
Equal.trans(U32, lhs, middle, rhs, lemma_one(xs), lemma_two(xs))
```

**Case analysis on a computed value.** You cannot `match f(x)`. Generalize:
take `c: Cmp` and `e: {f(x) == c : Cmp}` as parameters, match `c`, and pass
`f(x)` and `{==}` at the call site. If the lemma needs its own induction
hypothesis, pass it in as a function argument
(`ih: {A} -> {B} -> {C}`, supplied as `x => y => lemma(p, ..., x, y)`), since
mutual recursion is not allowed in proofs either.

**Proofs that must mirror a function's patterns.** For the goal to reduce, the
proof must split exactly the constructors the function inspects. Enumerate the
cases (generate them with a script if there are dozens); impossible ones close
with the absurd recipe because the validator reduces to `False` there.

**Injectivity via a round trip.** If `decode(encode(e)) == Some{e}` is proved,
then `encode(a) == encode(b)` gives `a == b`: rewrite both round trips, then
`Equal.cong` with `m => Maybe.default(&2, T, m, a)`.

**Total orders.** Prove antisymmetry, "EQ implies equal" and transitivity for
`Word.cmp` by induction on `n`, lift to U32 by matching `U32{w}`, then to
lexicographic list comparison. Comparing canonical encodings
lexicographically gives a total order on any datatype for free once the codec
round trip is proved.

## 5. Writing code that can be proved

The checker only unfolds what it can compute. Shape code so the goal reduces:

- Match the argument you know first, alone, and delegate the rest to a helper
  (`Rat.add(x, y)` with `match x:` for a `0 + y = y` shortcut). A multi-column
  match stays stuck on any unknown column.
- Add identity shortcuts (`0 + y`, `1 * y`, "already sorted, skip sorting"):
  they make fixed-point laws provable without proving arithmetic or sorting.
- Keep list order predictable: a right fold (`f(x, go(rest))`) preserves order;
  an accumulator reverses it and forces reverse lemmas.
- Prefer structural recursion, fuel, or stack machines to unsafe defs: induction
  is impossible over unsafe recursion.
- Separate *shape invariants* into a Bool validator (the law hypothesis) and
  test from Python that every result satisfies it before trying to prove
  closure.
- `Bool.and(a, b)` chains in validators should follow the order the proof
  needs them.

## 6. Mutation checking

For each new law: edit the code it covers (swap arguments, drop a term, return
a constant), run `bend PROOF.bend`, confirm it fails **at that law** (read the
`Location:` line), and restore the code. A failure caused by affinity or a
syntax error does not count; mutate semantically. Record that each law was
mutation-tested.

## 7. What a proof does and does not cover

- Laws are about Bend definitions in the Bend type theory. They do not cover
  the C bridge, CPython, the Python wrapper, or the generated runtime.
- U32 is proved about the Word model; the compiled code uses native integers.
- A proof that relies on unsafe defs holds only if those defs terminate on the
  inputs involved; `bend` lists them. Say so when reporting.
- Report proved, tested and trusted properties separately.
