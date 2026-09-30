# Bend 2 for bend-python work

The authoritative text is `bend guide` (pinned 2.0.28) and `bend base`. This
file condenses them and adds what the checker actually rejects, with fixes,
learned by building a computer algebra system in Bend.

## Contents

1. Syntax essentials
2. Quantities, kinds and affinity
3. Matching rules
4. Definition order, recursion and termination
5. Patterns for branching and recursion
6. Modules and imports
7. Numbers, strings and lists
8. Evaluation and performance
9. Checker errors and fixes

## 1. Syntax essentials

```bend
import Base

type Shape is Data:                 # Data: copyable; Type: affine
  Circle{r: U32}
  Square{s: U32}

def area(x: Shape) -> U32:
  match x:
    case Circle{+r}:                # +r: may be used more than once
      (3 * r * r : U32)             # operators need a type annotation
    case Square{+s}:
      (s * s : U32)

def main() -> U32:
  area(Square{5})
```

- No `if`: match on `True{}` / `False{}` (of a parameter, see §3).
- Operators are sugar for `T.add` etc.: `(a + b : U32)`; without `: T` they are
  `Nat`. Spaces around operators are required. Bit ops `.&. .|. .^.`, shifts
  `<< >>` take a `Nat` amount: `(x >> 8n : U32)`.
- Literals: `42` U32, `1.5` F32, `3n` Nat, `'c'` Char, `"s"` String,
  `[a, b]` list, `h <> t` cons, `(a, b)` pair, `K{a, b}` constructor.
- Equality of values is a call (`U32.is_eq(a, b)`, `Cmp` from `T.cmp`);
  `==` exists only inside types `{a == b : T}`.
- `x = v` let, `+x = v` reusable let, `(a, b) = p` destructuring let (only of a
  parameter or bound variable, not a computed value, see §3).
- `do IO<T>:` blocks: `x : A <- m` binds, `m` runs a Unit step, `return v`.
  Every bind is annotated. A destructuring let (`(a, b) = pair`) is not
  allowed inside a `do` block: bind the pair, then pass it to a helper def that
  destructures it and opens its own block (as `Python.binary` callers do).
- Comments start with `#`.

## 2. Quantities, kinds and affinity

- Variables are affine by default: used at most once. `+x` allows reuse and
  requires a Data type; `-x` is erased (types and proofs only).
- `type T is Data` is copyable; `is Type` is not. Closures, arrays and IO
  handles are Type.
- `List<A>` is `List<&1, A>` (affine). For reusable lists write `List<&2, A>`
  or `+List<A>`. A recursive datatype holding a list of itself must spell it
  out: `Add{terms: List<&2, Expr>}` inside `type Expr is Data`.
- Pairs `A & B` are `Kind(&1)` (Type), even of Data components, so
  `Maybe<&2, A & B>` fails. Define a small Data record instead:
  `type Parsed is Data: Parsed{value: Expr, rest: List<&2, U32>}`.
- Library functions often return `List<U32>` (affine). Either copy element by
  element into `List<&2, U32>` when you need reuse (and back before passing it
  on), or write list functions quantity-polymorphic like Base does, so one def
  serves both: `def count(a, xs: List<a, U32>, v: U32) -> U32`, called as
  `count(&1, data, v)` or `count(&2, xs, v)`. In proofs, specialize to `&2`:
  a value of kind `Kind(a)` for an unknown `a` cannot be reused.
- Reuse in proofs counts too: a hypothesis or argument used twice needs `+h` /
  `+x`, including law binders (`for +x: T`).
- Pattern binders inherit reusability from the matched value: after
  `def f(+n: Nat)`, `case 1n+p:` gives a reusable `p` (there is no `+1n+p`
  syntax). Likewise matching a `+xs` list hands out reusable `h` and `t`; on a
  plain value write `C{+field}` for the fields you reuse.

## 3. Matching rules

1. **Only parameters or pattern-bound variables can be scrutinized.**
   `match f(x):` is rejected, and so is `(a, b) = f(x)`. Pass the value to a
   helper def:

   ```bend
   def clamp.go(small: Bool, x: U32) -> U32:
     match small:
       case True{}:
         x
       case False{}:
         255

   def clamp(+x: U32) -> U32:
     clamp.go((x < 256 : U32), x)
   ```
2. **Scrutinees follow parameter order**, and no `let` may precede a `match`
   on a parameter. `def f(a, b, c)` may `match a c:` but not `match c a:`.
   Move lets into the cases.
3. **Match everything you need in one match.** A nested `match b:` inside a
   case of `match a:` on another *parameter* is rejected ("consumed binder").
   Use `match a b:` with combined patterns.
4. After the first pattern of a multi-scrutinee case, `[]` and `[x]` parse as
   indexing: write `Nil{}` and `Con{x, Nil{}}` (`case xs Nil{}:`).
5. Patterns nest (`case Mul{Num{c} <> fs}:`), match U32 literals
   (`case 0:`, `case 16 <> v <> rest:`), and a catch-all rebinds (`case e:`).
6. A pattern match compiles column by column. A function is stuck (does not
   reduce, which matters for proofs) until every column it inspects is a
   constructor, even if an earlier row would already decide. To make
   `f(x, y)` reduce when only `x` is known, match `x` alone and delegate.

## 4. Definition order, recursion and termination

- A def may call only defs **above** it in the file. Types may reference each
  other in any order. `python3 $SKILL/scripts/reorder_defs.py file.bend` topologically
  reorders a file's defs (unsafe defs are left unconstrained).
- **Mutual recursion is not allowed.** Merge two functions into one with a
  selector argument placed *after* the shrinking argument, or use unsafe defs.
- Recursion must be **structural**: the checker reads recursive-call arguments
  left to right; each must be passed unchanged until one is a smaller part of
  its parameter (obtained by matching). Put the shrinking parameter first.
  Rebuilding a smaller constructor also counts: in `case Add{t <> ts}:` the
  call `f(Add{ts})` is accepted.
- `def f?(x: A) -> B:` (or `@unsafe def`) skips the termination check and may
  call defs below it, so unsafe defs can be mutually recursive. `bend` then
  lists every def that "relies on unsafe or foreign code". Proofs can still
  unfold unsafe defs, but cannot do induction over their recursion.
- Recursion bounded by something non-structural takes a `Nat` fuel argument
  first: `go(fuel: Nat, ...)` recursing on `pred`. Pick fuel from the input
  (its length, or twice the digit count).

## 5. Patterns for branching and recursion

Strict evaluation: every argument is evaluated before the call. Choosing
between two recursive results by passing both to a selector computes both, which
is exponential for merges. Use these instead:

- **Select after recursing** when both branches recurse identically: compute
  the recursive result once and let a non-recursive helper choose how to use
  it (an excerpt: `Term.same_key` and `Term.next` are small non-recursive
  helpers):

  ```bend
  def Term.emit(same: Bool, cur: Term, rest: List<&2, Term>) -> List<&2, Term>:
    match same:
      case True{}:
        rest
      case False{}:
        cur <> rest

  def Term.merge(xs: List<&2, Term>, +cur: Term) -> List<&2, Term>:
    match xs:
      case []:
        [cur]
      case +h <> t:
        +same = Term.same_key(cur, h)
        Term.emit(same, cur, Term.merge(t, Term.next(same, cur, h)))
  ```
- **Precompute the decision as a parameter**: pass `cmp(x, head(rest))` as an
  argument of the recursive call, and match on it (a parameter) next time.
- **Mode flag** to fold a list and a tree walk into one structural def:
  `go(e: Expr, top: Bool, tail)` where `top = False` means "encode only the
  children of this Add". The expression stays first, so recursion on
  `Add{ts}` for the tail is structural.
- **Stack machines** decode token streams structurally: fold over the input
  list, pushing and popping a stack of items. No fuel, no unsafe, provable.

## 6. Modules and imports

```bend
import Base
import ./bend/python.bend as Python   # paths are relative to this file
import ./arith.bend as A
```

- Every imported name, **including constructors and types**, needs the alias:
  `A.square`, `A.Rat`, `E.Num{r}`, `Python.PyCall{args, kwargs}`.
- Module paths are plain names; directories are fine (`./src/pkg/expr.bend`),
  dots in file names are not (`a.b.bend` is refused).
- The alias belongs to the importer. Inside `logic.bend` write
  `def next(...)`, not `def Logic.next(...)`; callers write `Logic.next`.
  Defining `Logic.next` inside the module makes callers need
  `Logic.Logic.next`, and the error (`expected : a defined name / observed :
  src/pkg/logic.max_go`) points at the caller or law, not at the def.
- Dots inside def names are just characters (`Mag.add_c`), so a module whose
  defs start with `Mag.` is used as `Big.Mag.add_c`.
- Two files cannot import each other; move shared code down.
- Constructor names are global within a program: two types cannot both have
  `Fail{}`.

## 7. Numbers, strings and lists

- `U32` is native at runtime but modelled in the type theory as
  `U32{Word(32n)}`, 32 Booleans, so proofs about concrete words compute bit by
  bit. `U32.cmp`, `U32.is_eq`, `U32.div`, `U32.mod`, `U32.log2`, `U32.to_nat`
  exist (`bend base U32`). Only `U32.add_comm` is proved in Base.
- `Nat` is Peano in the theory (fast at runtime). Literal patterns `0n`,
  `1n+p`. `U32.to_nat`, `Nat.divmod`, `Nat.cmp`.
- `F32` only; there is no 64-bit float. For doubles, call Python (PYTHON.md).
- `String` is a linked list of `Chr{code}`; slow. Prefer byte lists
  (`List<U32>` of values below 256) and `Python.to_bytes`/`from_bytes`.
- Arbitrary precision: none built in. Represent big integers as little-endian
  digit lists; base 256 keeps each digit a byte for the wire, and products of
  digits fit a U32.
- `List.sort(~A, ~le, +xs)` is a merge sort; `List.reverse(&2, A, xs)`,
  `List.append(&2, A, xs, ys)`, `List.length`, `Maybe.default(&2, A, m, d)`.
  Quantity arguments (`&2`) and type arguments are explicit.
- `Bool.pick(-A, c, a, b)` evaluates both `a` and `b` (strict): fine for
  constants, wrong for recursion.

## 8. Evaluation and performance

- Evaluation is strict and there is no garbage collector: a match frees what
  it opens, `+` values are reference counted.
- `bend file.bend` with a non-IO `main` normalizes `main` in the **checker**,
  which is slow for real work (it can take minutes). Test through the built
  extension instead.
- Each call into the extension gets a runtime instance and one worker thread.
  Parallel calls (`a b = f(x) g(y)`) are not parallel under bend-python.
- Decoding and re-encoding per call is cheap compared to Python recursion:
  a 286-term polynomial prints in ~5 ms.

## 9. Checker errors and fixes

| Message (abridged) | Cause | Fix |
|---|---|---|
| `a match cannot scrutinize a computed value` | `match f(x):` or `(a, b) = f(x)` | Helper def taking the value as a parameter |
| `a match on a parameter or field (this name is a def or a consumed binder)` | Nested match on a parameter, wrong scrutinee order, or a let before the match | One `match a b:` in parameter order; lets inside cases |
| `expected a filled definition ... observed <name>` | Calling a def defined below (or an unproved law) | Reorder (`python3 $SKILL/scripts/reorder_defs.py`), or make the caller unsafe |
| `<x> (consumed more than once)` | Affine variable used twice | `+x` on the parameter, pattern field, let or law binder |
| `expected : Data  observed : Type` | Affine list/pair inside Data | `List<&2, T>`, a Data record type instead of `A & B` |
| `expected : -G  observed : G` | Erased parameter used in a returned type | Drop the `-` |
| `expected a term, observed ']'` | `[]` after the first pattern | `Nil{}`, `Con{x, Nil{}}` |
| `expected 2 patterns (one per scrutinee)` | Same parsing issue | Same fix |
| `expected: a pattern (a binder or a constructor)  observed: IO.bind(...)` | Destructuring let inside a `do` block | Destructure in a helper def |
| `expected a fresh constructor name (duplicate declaration: X)` | Constructor name reused | Rename |
| `expected : a defined name  observed : src/pkg/logic.f` | Defs inside `logic.bend` were named `Logic.f` | Name them `f`; the alias is the importer's |
| `expected : a defined name  observed : _` | `_` passed as an ordinary argument (e.g. an erased type) | Spell the argument out; `_` only works inside a `%e : P` motive |
| `expected a fresh name (Laws is an import's alias)` | Helper lemma named `Laws.foo` | Only laws use the `Laws.` prefix; name helpers freely |
| `expected a quantified datatype after +` | `+U32` inside a type argument | `List<&2, U32>` |
| `expected : List<&2, U32> observed : List<U32>` | Mixed quantities | Copy the list, or align the signature |
| Goal mismatch on `%e : P` | Rewrite direction | See PROOFS.md |
| `cycle through X` (from reorder_defs.py) | Mutual recursion among safe defs | Merge the defs or make one unsafe |
