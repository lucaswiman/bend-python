import Base
import ./THREAD_LAWS.bend as Laws
import ./thread_state.bend as Thread

def Laws.detached_effect_rejected():
  {==}

def none_discriminator(value: Maybe<Thread.State>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-next: Thread.State, accepted: {None{} == Some{next} : Maybe<Thread.State>}) -> Empty:
  %accepted : none_discriminator(_)
  Unit{}

def Laws.accepted_effect_attached(state, next, accepted):
  match state:
    case Thread.Attached{}:
      {==}
    case Thread.Detached{}:
      Empty.absurd({Thread.Detached{} == Thread.Attached{} : Thread.State}, none_not_some(next, accepted))

def Laws.save_restore():
  {==}

def Laws.native_returns_attached(release_gil, outcome):
  match release_gil:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.native_then_effect_accepted(release_gil, outcome):
  match release_gil:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.release_before_restore_rejected(outcome):
  {==}

def Laws.acquire_returns_attached():
  {==}

def Laws.return_restores_attachment(outcome, cache_space):
  {==}

def Laws.reserved_unavailable(pool, slot):
  match pool slot:
    case [] _:
      {==}
    case _ <> _ 0n:
      {==}
    case _ <> tail 1n+pred:
      Laws.reserved_unavailable(tail, pred)

def Laws.reserved_acquisition_rejected(pool, slot):
  %Laws.reserved_unavailable(pool, slot) : {Thread.acquire(Thread.reserve(pool, slot), slot) == Thread.acquire_if(_, Thread.reserve(pool, slot), slot) : Maybe<List<&2, Thread.Context>>}
  {==}

def true_discriminator(value: Bool) -> Type:
  match value:
    case True{}:
      Unit
    case False{}:
      Empty

def true_not_false(different: {True{} == False{} : Bool}) -> Empty:
  %different : true_discriminator(_)
  Unit{}

def Laws.context_update_isolated(pool, target, other, value, different):
  match pool target other:
    case [] _ _:
      {==}
    case head <> _ 0n 0n:
      Empty.absurd({Some{value} == Some{head} : Maybe<&2, Thread.Context>}, true_not_false(different))
    case _ <> _ 0n 1n+_:
      {==}
    case _ <> _ 1n+_ 0n:
      {==}
    case _ <> tail 1n+pred 1n+rest:
      Laws.context_update_isolated(tail, pred, rest, value, different)

def Laws.failure_isolated(cache_space, pool, target, other, different):
  Laws.context_update_isolated(pool, target, other, Thread.Discarded{}, different)

def Laws.failed_context_discarded(cache_space):
  {==}

def Laws.failed_context_unavailable(cache_space, pool, slot):
  match pool slot:
    case [] _:
      {==}
    case _ <> _ 0n:
      {==}
    case _ <> tail 1n+pred:
      Laws.failed_context_unavailable(cache_space, tail, pred)

def Laws.successful_context_reusable():
  {==}
