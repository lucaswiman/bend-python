import Base
import ./python.bend as Python
import ./TENSOR_LAWS.bend as Laws
import ./tensor_state.bend as State
import ./tensor.bend as Tensor

# Constructor discrimination only; no arithmetic assumptions about F32.
def false_at_true(value: Bool) -> Type:
  match value:
    case True{}:
      Empty
    case False{}:
      Unit

def false_ne_true(equal: {False{} == True{} : Bool}) -> Empty:
  %equal : false_at_true(_)
  Unit{}

def true_not_false(equal: {True{} == False{} : Bool}) -> Empty:
  false_ne_true(Equal.sym(Bool, True{}, False{}, equal))

def none_case(value: Maybe<&2, F32>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-value: F32, equal: {None{} == Some{value} : Maybe<&2, F32>}) -> Empty:
  %equal : none_case(_)
  Unit{}

def Laws.replace_preserves_count(memory, index, value):
  match memory index:
    case [] _:
      {==}
    case _ <> tail 0n:
      {==}
    case _ <> tail 1n+pred:
      Equal.cong(Nat, Nat, x => 1n+x, List.length(&2, F32, State.replace(tail, pred, value)), List.length(&2, F32, tail), Laws.replace_preserves_count(tail, pred, value))

def Laws.read_after_write(memory, index, value, old, present):
  match memory index:
    case [] _:
      Empty.absurd({None{} == Some{value} : Maybe<&2, F32>}, none_not_some(old, present))
    case _ <> tail 0n:
      {==}
    case _ <> tail 1n+pred:
      Laws.read_after_write(tail, pred, value, old, present)

def Laws.other_cells_unchanged(memory, written, observed, value, different):
  match memory written observed:
    case [] _ _:
      {==}
    case head <> tail 0n 0n:
      Empty.absurd({Some{value} == Some{head} : Maybe<&2, F32>}, true_not_false(different))
    case _ <> _ 0n 1n+_:
      {==}
    case _ <> _ 1n+_ 0n:
      {==}
    case _ <> tail 1n+wi 1n+ri:
      Laws.other_cells_unchanged(tail, wi, ri, value, different)

def Laws.read_only_rejected(memory, index, value):
  {==}

def Laws.released_write_rejected(memory, index, value):
  {==}

def Laws.out_of_bounds_rejected(memory, index, value, outside):
  %Equal.sym(Bool, Nat.is_lt(index, List.length(&2, F32, memory)), False{}, outside) : {State.accept(_, memory, index, value) == None{} : Maybe<List<&2, F32>>}
  {==}

def Laws.writable_index_accepted(memory, index, value, inside):
  %Equal.sym(Bool, Nat.is_lt(index, List.length(&2, F32, memory)), True{}, inside) : {State.accept(_, memory, index, value) == Some{State.replace(memory, index, value)} : Maybe<List<&2, F32>>}
  {==}

def Laws.close_live(writable):
  {==}

def Laws.double_close_rejected():
  {==}

# Arithmetic helpers for structural Nat cursors; all recurse on constructors.
def sub_zero(n: Nat) -> {Nat.sub(n, 0n) == n : Nat}:
  match n:
    case 0n:
      {==}
    case 1n+pred:
      {==}

def add_zero(n: Nat) -> {Nat.add(n, 0n) == n : Nat}:
  match n:
    case 0n:
      {==}
    case 1n+pred:
      Equal.cong(Nat, Nat, x => 1n+x, Nat.add(pred, 0n), pred, add_zero(pred))

def add_succ(a: Nat, b: Nat) -> {Nat.add(a, 1n+b) == 1n+Nat.add(a, b) : Nat}:
  match a:
    case 0n:
      {==}
    case 1n+pred:
      Equal.cong(Nat, Nat, x => 1n+x, Nat.add(pred, 1n+b), 1n+Nat.add(pred, b), add_succ(pred, b))

def lt_succ(a: Nat, b: Nat, less: {Nat.is_lt(a, b) == True{} : Bool}) -> {Nat.is_lt(a, 1n+b) == True{} : Bool}:
  match a b:
    case 0n 0n:
      {==}
    case 0n 1n+bp:
      {==}
    case 1n+ap 0n:
      Empty.absurd({Nat.is_lt(1n+ap, 1n) == True{} : Bool}, false_ne_true(less))
    case 1n+ap 1n+bp:
      lt_succ(ap, bp, less)

def le_succ(a: Nat, b: Nat, bounded: {Nat.is_le(a, b) == True{} : Bool}) -> {Nat.is_le(a, 1n+b) == True{} : Bool}:
  match a b:
    case 0n 0n:
      {==}
    case 0n 1n+bp:
      {==}
    case 1n+ap 0n:
      Empty.absurd({Nat.is_le(1n+ap, 1n) == True{} : Bool}, false_ne_true(bounded))
    case 1n+ap 1n+bp:
      le_succ(ap, bp, bounded)

def self_lt_succ(n: Nat) -> {Nat.is_lt(n, 1n+n) == True{} : Bool}:
  match n:
    case 0n:
      {==}
    case 1n+pred:
      self_lt_succ(pred)

def Laws.cursor_initial(length):
  match length:
    case 0n:
      {==}
    case 1n+pred:
      Laws.cursor_initial(pred)

def Laws.cursor_terminal(length):
  sub_zero(length)

def Laws.cursor_conservation(length, remaining, bounded):
  match length remaining:
    case 0n 0n:
      {==}
    case 0n 1n+pred:
      Empty.absurd({Nat.add(State.position(0n, 1n+pred), 1n+pred) == 0n : Nat}, false_ne_true(bounded))
    case 1n+lp 0n:
      add_zero(1n+lp)
    case 1n+lp 1n+rp:
      Equal.trans(Nat, Nat.add(State.position(lp, rp), 1n+rp), 1n+Nat.add(State.position(lp, rp), rp), 1n+lp, add_succ(State.position(lp, rp), rp), Equal.cong(Nat, Nat, x => 1n+x, Nat.add(State.position(lp, rp), rp), lp, Laws.cursor_conservation(lp, rp, bounded)))

def Laws.cursor_in_bounds(length, pred, bounded):
  match length pred:
    case 0n _:
      Empty.absurd({Nat.is_lt(State.position(0n, 1n+pred), 0n) == True{} : Bool}, false_ne_true(bounded))
    case 1n+lp 0n:
      %Equal.sym(Nat, Nat.sub(lp, 0n), lp, sub_zero(lp)) : {Nat.is_lt(_, 1n+lp) == True{} : Bool}
      self_lt_succ(lp)
    case 1n+lp 1n+pp:
      lt_succ(State.position(lp, 1n+pp), lp, Laws.cursor_in_bounds(lp, pp, bounded))

def advance_nat(+length: Nat, +pred: Nat, bounded: {Nat.is_le(1n+pred, length) == True{} : Bool}) -> {State.position(length, pred) == 1n+State.position(length, 1n+pred) : Nat}:
  match length pred:
    case 0n _:
      Empty.absurd({State.position(0n, pred) == 1n+State.position(0n, 1n+pred) : Nat}, false_ne_true(bounded))
    case 1n+lp 0n:
      Equal.cong(Nat, Nat, x => 1n+x, lp, Nat.sub(lp, 0n), Equal.sym(Nat, Nat.sub(lp, 0n), lp, sub_zero(lp)))
    case 1n+lp 1n+pp:
      advance_nat(lp, pp, bounded)

def Laws.cursor_advance(length, pred, bounded):
  %Equal.sym(Nat, Nat.sub(pred, 0n), pred, sub_zero(pred)) : {State.position(length, _) == 1n+State.position(length, 1n+pred) : Nat}
  advance_nat(length, pred, bounded)

def decrease_nat(+length: Nat, +pred: Nat, bounded: {Nat.is_le(1n+pred, length) == True{} : Bool}) -> {Nat.is_le(pred, length) == True{} : Bool}:
  match length:
    case 0n:
      Empty.absurd({Nat.is_le(pred, 0n) == True{} : Bool}, false_ne_true(bounded))
    case 1n+lp:
      le_succ(pred, lp, bounded)

def Laws.cursor_decrease(length, pred, bounded):
  %Equal.sym(Nat, Nat.sub(pred, 0n), pred, sub_zero(pred)) : {Nat.is_le(_, length) == True{} : Bool}
  decrease_nat(length, pred, bounded)

def Laws.map_zero(memory):
  {==}

def Laws.map_step(f, count, value):
  {==}

def Laws.map_count(count, steps):
  match count:
    case 0n:
      {==}
    case 1n+pred:
      Equal.cong(Nat, Nat, n => 1n+n, State.step_count(pred, Pair.snd(F32, Python.F32MapSteps(pred), steps(0.0))), pred, Laws.map_count(pred, Pair.snd(F32, Python.F32MapSteps(pred), steps(0.0))))

def Laws.map_elements(f, memory):
  match memory:
    case []:
      {==}
    case head <> tail:
      Equal.cong(List<F32>, List<F32>, values => f(head) <> values, State.apply_steps(List.length(&1, F32, tail), tail, Tensor.map_steps(~f, List.length(&1, F32, tail))), List.map(~F32, ~F32, ~f, tail), Laws.map_elements(f, tail))
