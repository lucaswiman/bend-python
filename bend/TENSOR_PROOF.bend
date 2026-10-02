import Base
import ./TENSOR_LAWS.bend as Laws
import ./tensor_state.bend as State

# Constructor discrimination only; no arithmetic assumptions about F32.
def none_case(value: Maybe<&2, F32>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-value: F32, equal: {None{} == Some{value} : Maybe<&2, F32>}) -> Empty:
  %equal : none_case(_)
  Unit{}

def false_case(value: Bool) -> Type:
  match value:
    case True{}:
      Unit
    case False{}:
      Empty

def true_not_false(equal: {True{} == False{} : Bool}) -> Empty:
  %equal : false_case(_)
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

def Laws.map_zero(f, view, index):
  {==}

def Laws.map_step(f, count, view, index):
  {==}
