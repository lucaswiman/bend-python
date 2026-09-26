import Base
import ./THREAD_LAWS.bend as Laws
import ./thread_state.bend as Thread

# Discriminators

def none_discriminator(value: Maybe<Thread.State>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-next: Thread.State, accepted: {None{} == Some{next} : Maybe<Thread.State>}) -> Empty:
  %accepted : none_discriminator(_)
  Unit{}

def true_discriminator(value: Bool) -> Type:
  match value:
    case True{}:
      Unit
    case False{}:
      Empty

def true_not_false(different: {True{} == False{} : Bool}) -> Empty:
  %different : true_discriminator(_)
  Unit{}

def false_discriminator(value: Bool) -> Type:
  match value:
    case True{}:
      Empty
    case False{}:
      Unit

def false_not_true(different: {False{} == True{} : Bool}) -> Empty:
  %different : false_discriminator(_)
  Unit{}

def not_true(-A: Type, different: {False{} == True{} : Bool}) -> A:
  Empty.absurd(A, false_not_true(different))

def not_false(-A: Type, different: {True{} == False{} : Bool}) -> A:
  Empty.absurd(A, true_not_false(different))

# Python effects

def Laws.detached_effect_rejected():
  {==}

def Laws.accepted_effect_attached(state, next, accepted):
  match state:
    case Thread.Attached{}:
      {==}
    case Thread.Detached{}:
      Empty.absurd({Thread.Detached{} == Thread.Attached{} : Thread.State}, none_not_some(next, accepted))
    case Thread.AttachedNative{}:
      Empty.absurd({Thread.AttachedNative{} == Thread.Attached{} : Thread.State}, none_not_some(next, accepted))
    case Thread.DetachedNative{}:
      Empty.absurd({Thread.DetachedNative{} == Thread.Attached{} : Thread.State}, none_not_some(next, accepted))

def Laws.native_effect_rejected(state, native):
  match state:
    case Thread.Attached{}:
      not_true({Some{Thread.Attached{}} == None{} : Maybe<Thread.State>}, native)
    case Thread.Detached{}:
      {==}
    case Thread.AttachedNative{}:
      {==}
    case Thread.DetachedNative{}:
      {==}

def Laws.native_only_leaves(state, operation, native, staying):
  match state operation:
    case Thread.Attached{} op:
      not_true({Thread.step(Thread.Attached{}, op) == None{} : Maybe<Thread.State>}, native)
    case Thread.Detached{} op:
      not_true({Thread.step(Thread.Detached{}, op) == None{} : Maybe<Thread.State>}, native)
    case Thread.AttachedNative{} Thread.Leave{outcome}:
      not_false({Some{Thread.Attached{}} == None{} : Maybe<Thread.State>}, staying)
    case Thread.DetachedNative{} Thread.Leave{outcome}:
      not_false({Some{Thread.Detached{}} == None{} : Maybe<Thread.State>}, staying)
    case Thread.AttachedNative{} Thread.Save{}:
      {==}
    case Thread.AttachedNative{} Thread.Restore{}:
      {==}
    case Thread.AttachedNative{} Thread.Enter{}:
      {==}
    case Thread.AttachedNative{} Thread.PythonEffect{}:
      {==}
    case Thread.AttachedNative{} Thread.AcquireLease{_}:
      {==}
    case Thread.AttachedNative{} Thread.ReturnLease{_, _, _, _}:
      {==}
    case Thread.DetachedNative{} Thread.Save{}:
      {==}
    case Thread.DetachedNative{} Thread.Restore{}:
      {==}
    case Thread.DetachedNative{} Thread.Enter{}:
      {==}
    case Thread.DetachedNative{} Thread.PythonEffect{}:
      {==}
    case Thread.DetachedNative{} Thread.AcquireLease{_}:
      {==}
    case Thread.DetachedNative{} Thread.ReturnLease{_, _, _, _}:
      {==}

# Attachment

def Laws.save_restore():
  {==}

def Laws.native_returns_attached(detach, outcome):
  match detach:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.native_then_effect_accepted(detach, outcome):
  match detach:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.release_before_restore_rejected(outcome):
  {==}

def Laws.free_threaded_detaches(release_gil):
  match release_gil:
    case True{}:
      {==}
    case False{}:
      {==}

# Leases

def Laws.acquire_requires_detached(state, pool, slot, attached):
  match state:
    case Thread.Attached{}:
      {==}
    case Thread.Detached{}:
      Empty.absurd({Thread.detached_with(Thread.acquire(pool, slot)) == None{} : Maybe<Thread.Session>}, attached({==}))
    case Thread.AttachedNative{}:
      {==}
    case Thread.DetachedNative{}:
      {==}

def Laws.return_requires_detached(state, pool, slot, outcome, small_heap, cache_space, attached):
  match state:
    case Thread.Attached{}:
      {==}
    case Thread.Detached{}:
      Empty.absurd({Thread.detached_with(Thread.release(pool, slot, outcome, small_heap, cache_space)) == None{} : Maybe<Thread.Session>}, attached({==}))
    case Thread.AttachedNative{}:
      {==}
    case Thread.DetachedNative{}:
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

def pool_none_discriminator(value: Maybe<List<&2, Thread.Context>>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def pool_none_not_some(-next: List<&2, Thread.Context>,
  acquired: {None{} == Some{next} : Maybe<List<&2, Thread.Context>>}) -> Empty:
  %acquired : pool_none_discriminator(_)
  Unit{}

def acquire_if_allowed(allowed: Bool, -pool: List<&2, Thread.Context>, -slot: Nat,
  -next: List<&2, Thread.Context>,
  acquired: {Thread.acquire_if(allowed, pool, slot) == Some{next} : Maybe<List<&2, Thread.Context>>})
  -> {allowed == True{} : Bool}:
  match allowed:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({False{} == True{} : Bool}, pool_none_not_some(next, acquired))

def Laws.acquisition_requires_available(pool, slot, next, acquired):
  acquire_if_allowed(Thread.available(pool, slot), pool, slot, next, acquired)

def Laws.reserved_returnable(pool, slot, free):
  match pool slot:
    case [] _:
      not_true({False{} == True{} : Bool}, free)
    case _ <> _ 0n:
      {==}
    case _ <> tail 1n+pred:
      Laws.reserved_returnable(tail, pred, free)

def Laws.unleased_return_rejected(pool, slot, outcome, small_heap, cache_space, unleased):
  %unleased : {Thread.release(pool, slot, outcome, small_heap, cache_space) == Thread.finish_if(_, pool, slot, outcome, small_heap, cache_space) : Maybe<List<&2, Thread.Context>>}
  {==}

def completion_not_leased(outcome: Thread.Outcome, small_heap: Bool, cache_space: Bool)
  -> {Thread.leased(Some{Thread.completion(outcome, small_heap, cache_space)}) == False{} : Bool}:
  match outcome small_heap cache_space:
    case Thread.Succeeded{} True{} True{}:
      {==}
    case Thread.Succeeded{} True{} False{}:
      {==}
    case Thread.Succeeded{} False{} _:
      {==}
    case Thread.Failed{} _ _:
      {==}

def Laws.double_return_rejected(pool, slot, outcome, small_heap, cache_space):
  match pool slot:
    case [] _:
      {==}
    case _ <> _ 0n:
      completion_not_leased(outcome, small_heap, cache_space)
    case _ <> tail 1n+pred:
      Laws.double_return_rejected(tail, pred, outcome, small_heap, cache_space)

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

def Laws.failure_isolated(small_heap, cache_space, pool, target, other, different):
  Laws.context_update_isolated(pool, target, other, Thread.Discarded{}, different)

# Reuse

def Laws.failed_context_discarded(small_heap, cache_space):
  {==}

def Laws.failed_context_unavailable(small_heap, cache_space, pool, slot):
  match pool slot:
    case [] _:
      {==}
    case _ <> _ 0n:
      {==}
    case _ <> tail 1n+pred:
      Laws.failed_context_unavailable(small_heap, cache_space, tail, pred)

def Laws.successful_context_reusable():
  {==}

def idle_discriminator(value: Thread.Context) -> Type:
  match value:
    case Thread.Idle{}:
      Empty
    case Thread.Leased{}:
      Unit
    case Thread.Discarded{}:
      Unit

def discarded_empty(reusable: {Thread.Discarded{} == Thread.Idle{} : Thread.Context}) -> Empty:
  %reusable : idle_discriminator(_)
  Unit{}

def discarded_not_idle(-A: Type, reusable: {Thread.Discarded{} == Thread.Idle{} : Thread.Context}) -> A:
  Empty.absurd(A, discarded_empty(reusable))

def Laws.reusable_requires_success(outcome, small_heap, cache_space, reusable):
  match outcome small_heap cache_space:
    case Thread.Succeeded{} _ _:
      {==}
    case Thread.Failed{} _ _:
      discarded_not_idle({Thread.Failed{} == Thread.Succeeded{} : Thread.Outcome}, reusable)

def Laws.reusable_requires_small_heap(outcome, small_heap, cache_space, reusable):
  match outcome small_heap cache_space:
    case _ True{} _:
      {==}
    case Thread.Succeeded{} False{} _:
      discarded_not_idle({False{} == True{} : Bool}, reusable)
    case Thread.Failed{} False{} _:
      discarded_not_idle({False{} == True{} : Bool}, reusable)

def Laws.reusable_requires_cache_space(outcome, small_heap, cache_space, reusable):
  match outcome small_heap cache_space:
    case _ _ True{}:
      {==}
    case Thread.Succeeded{} True{} False{}:
      discarded_not_idle({False{} == True{} : Bool}, reusable)
    case Thread.Succeeded{} False{} False{}:
      discarded_not_idle({False{} == True{} : Bool}, reusable)
    case Thread.Failed{} _ False{}:
      discarded_not_idle({False{} == True{} : Bool}, reusable)

# Whole invocations

def Laws.invocation_returns_attached(rest, detach, outcome, small_heap, cache_space):
  match detach:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.leased_invocation_rejected(rest, detach, outcome, small_heap, cache_space):
  match detach:
    case True{}:
      {==}
    case False{}:
      {==}
