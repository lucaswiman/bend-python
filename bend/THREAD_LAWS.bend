import Base
import ./thread_state.bend as Thread

# Python effects

law detached_effect_rejected:
  {Thread.step(Thread.Detached{}, Thread.PythonEffect{}) == None{} : Maybe<Thread.State>}

law accepted_effect_attached:
  for state: Thread.State
  for next: Thread.State
  for accepted: {Thread.step(state, Thread.PythonEffect{}) == Some{next} : Maybe<Thread.State>}
  {state == Thread.Attached{} : Thread.State}

# Inside native evaluation, no Python effect, save, restore, lease operation,
# or nested entry is accepted: the only way out is Leave.
law native_effect_rejected:
  for state: Thread.State
  for native: {Thread.is_native(state) == True{} : Bool}
  {Thread.step(state, Thread.PythonEffect{}) == None{} : Maybe<Thread.State>}

law native_only_leaves:
  for state: Thread.State
  for operation: Thread.Operation
  for native: {Thread.is_native(state) == True{} : Bool}
  for staying: {Thread.is_leave(operation) == False{} : Bool}
  {Thread.step(state, operation) == None{} : Maybe<Thread.State>}

# Attachment

law save_restore:
  {Thread.run([Thread.Save{}, Thread.Restore{}], Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

# Both detach policies and both outcomes (a native failure also leaves through
# Leave) return to the attached state.
law native_returns_attached:
  for detach: Bool
  for outcome: Thread.Outcome
  {Thread.run(Thread.native_call(detach, outcome), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law native_then_effect_accepted:
  for detach: Bool
  for outcome: Thread.Outcome
  {Thread.run(List.append(&1, Thread.Operation, Thread.native_call(detach, outcome), [Thread.PythonEffect{}]), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law release_before_restore_rejected:
  for outcome: Thread.Outcome
  {Thread.run([Thread.Save{}, Thread.Enter{}, Thread.Leave{outcome}, Thread.PythonEffect{}], Thread.Attached{}) == None{} : Maybe<Thread.State>}

law free_threaded_detaches:
  for release_gil: Bool
  {Thread.detaches(release_gil, True{}) == True{} : Bool}

# Leases

law acquire_requires_detached:
  for state: Thread.State
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for attached: {state != Thread.Detached{} : Thread.State}
  {Thread.step_session(Thread.Session{state, pool}, Thread.AcquireLease{slot}) == None{} : Maybe<Thread.Session>}

law return_requires_detached:
  for state: Thread.State
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  for attached: {state != Thread.Detached{} : Thread.State}
  {Thread.step_session(Thread.Session{state, pool}, Thread.ReturnLease{slot, outcome, small_heap, cache_space}) == None{} : Maybe<Thread.Session>}

law reserved_unavailable:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.available(Thread.reserve(pool, slot), slot) == False{} : Bool}

law reserved_acquisition_rejected:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.acquire(Thread.reserve(pool, slot), slot) == None{} : Maybe<List<&2, Thread.Context>>}

law acquisition_requires_available:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for next: List<&2, Thread.Context>
  for acquired: {Thread.acquire(pool, slot) == Some{next} : Maybe<List<&2, Thread.Context>>}
  {Thread.available(pool, slot) == True{} : Bool}

law reserved_returnable:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for free: {Thread.available(pool, slot) == True{} : Bool}
  {Thread.returnable(Thread.reserve(pool, slot), slot) == True{} : Bool}

law unleased_return_rejected:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  for unleased: {Thread.returnable(pool, slot) == False{} : Bool}
  {Thread.release(pool, slot, outcome, small_heap, cache_space) == None{} : Maybe<List<&2, Thread.Context>>}

law double_return_rejected:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  {Thread.returnable(Thread.finish(pool, slot, outcome, small_heap, cache_space), slot) == False{} : Bool}

# Updating one slot leaves every other slot unchanged. In C, isolation comes
# from separate allocations and thread-local bp_current; this is the model's
# counterpart, a List.set/List.get lemma.
law context_update_isolated:
  for pool: List<&2, Thread.Context>
  for target: Nat
  for other: Nat
  for value: Thread.Context
  for different: {Nat.is_eq(target, other) == False{} : Bool}
  {List.get(&2, Thread.Context, List.set(&2, Thread.Context, pool, target, value), other) == List.get(&2, Thread.Context, pool, other) : Maybe<&2, Thread.Context>}

law failure_isolated:
  for small_heap: Bool
  for cache_space: Bool
  for pool: List<&2, Thread.Context>
  for target: Nat
  for other: Nat
  for different: {Nat.is_eq(target, other) == False{} : Bool}
  {List.get(&2, Thread.Context, Thread.finish(pool, target, Thread.Failed{}, small_heap, cache_space), other) == List.get(&2, Thread.Context, pool, other) : Maybe<&2, Thread.Context>}

# Reuse

law failed_context_discarded:
  for small_heap: Bool
  for cache_space: Bool
  {Thread.completion(Thread.Failed{}, small_heap, cache_space) == Thread.Discarded{} : Thread.Context}

law failed_context_unavailable:
  for small_heap: Bool
  for cache_space: Bool
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.available(Thread.finish(pool, slot, Thread.Failed{}, small_heap, cache_space), slot) == False{} : Bool}

law successful_context_reusable:
  {Thread.completion(Thread.Succeeded{}, True{}, True{}) == Thread.Idle{} : Thread.Context}

law reusable_requires_success:
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  for reusable: {Thread.completion(outcome, small_heap, cache_space) == Thread.Idle{} : Thread.Context}
  {outcome == Thread.Succeeded{} : Thread.Outcome}

law reusable_requires_small_heap:
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  for reusable: {Thread.completion(outcome, small_heap, cache_space) == Thread.Idle{} : Thread.Context}
  {small_heap == True{} : Bool}

law reusable_requires_cache_space:
  for outcome: Thread.Outcome
  for small_heap: Bool
  for cache_space: Bool
  for reusable: {Thread.completion(outcome, small_heap, cache_space) == Thread.Idle{} : Thread.Context}
  {cache_space == True{} : Bool}

# Whole invocations: the thread-state and lease models together.

# From any idle slot, an invocation with any number of rounds, detach policy,
# and native outcomes returns attached, leaving only its own slot updated.
law invocation_returns_attached:
  for +pool: List<&2, Thread.Context>
  for +slot: Nat
  for +detach: Bool
  for +outcomes: List<&2, Thread.Outcome>
  for +small_heap: Bool
  for +cache_space: Bool
  for +free: {Thread.available(pool, slot) == True{} : Bool}
  {Thread.run_session(Thread.invocation(slot, detach, outcomes, small_heap, cache_space), Thread.Session{Thread.Attached{}, pool}) == Some{Thread.Session{Thread.Attached{}, Thread.finish(pool, slot, Thread.health(outcomes), small_heap, cache_space)}} : Maybe<Thread.Session>}

law leased_invocation_rejected:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for detach: Bool
  for outcomes: List<&2, Thread.Outcome>
  for small_heap: Bool
  for cache_space: Bool
  for busy: {Thread.available(pool, slot) == False{} : Bool}
  {Thread.run_session(Thread.invocation(slot, detach, outcomes, small_heap, cache_space), Thread.Session{Thread.Attached{}, pool}) == None{} : Maybe<Thread.Session>}

# A native failure in any round poisons the lease, so it is never cached.
law failure_poisons_lease:
  for before: List<&2, Thread.Outcome>
  for after: List<&2, Thread.Outcome>
  {Thread.health(List.append(&2, Thread.Outcome, before, Thread.Failed{} <> after)) == Thread.Failed{} : Thread.Outcome}
