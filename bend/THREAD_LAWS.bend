import Base
import ./thread_state.bend as Thread

law detached_effect_rejected:
  {Thread.step(Thread.Detached{}, Thread.PythonEffect{}) == None{} : Maybe<Thread.State>}

law accepted_effect_attached:
  for state: Thread.State
  for next: Thread.State
  for accepted: {Thread.step(state, Thread.PythonEffect{}) == Some{next} : Maybe<Thread.State>}
  {state == Thread.Attached{} : Thread.State}

law save_restore:
  {Thread.run([Thread.Save{}, Thread.Restore{}], Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

# Each context's normal and error completion follows the same restore policy.
law native_returns_attached:
  for release_gil: Bool
  for outcome: Thread.Outcome
  {Thread.run(Thread.native_call(release_gil, outcome), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law native_then_effect_accepted:
  for release_gil: Bool
  for outcome: Thread.Outcome
  {Thread.run(List.append(&1, Thread.Operation, Thread.native_call(release_gil, outcome), [Thread.PythonEffect{}]), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law release_before_restore_rejected:
  for outcome: Thread.Outcome
  {Thread.run([Thread.Save{}, Thread.NativeWork{outcome}, Thread.PythonEffect{}], Thread.Attached{}) == None{} : Maybe<Thread.State>}

law acquire_returns_attached:
  {Thread.run(Thread.acquire_call(), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law return_restores_attachment:
  for outcome: Thread.Outcome
  for cache_space: Bool
  {Thread.run(Thread.return_call(outcome, cache_space), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law reserved_unavailable:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.available(Thread.reserve(pool, slot), slot) == False{} : Bool}

law reserved_acquisition_rejected:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.acquire(Thread.reserve(pool, slot), slot) == None{} : Maybe<List<&2, Thread.Context>>}

# A context's success/failure cannot change any other allocation's state.
law context_update_isolated:
  for pool: List<&2, Thread.Context>
  for target: Nat
  for other: Nat
  for value: Thread.Context
  for different: {Nat.is_eq(target, other) == False{} : Bool}
  {List.get(&2, Thread.Context, List.set(&2, Thread.Context, pool, target, value), other) == List.get(&2, Thread.Context, pool, other) : Maybe<&2, Thread.Context>}

law failure_isolated:
  for cache_space: Bool
  for pool: List<&2, Thread.Context>
  for target: Nat
  for other: Nat
  for different: {Nat.is_eq(target, other) == False{} : Bool}
  {List.get(&2, Thread.Context, Thread.finish(pool, target, Thread.Failed{}, cache_space), other) == List.get(&2, Thread.Context, pool, other) : Maybe<&2, Thread.Context>}

law failed_context_discarded:
  for cache_space: Bool
  {Thread.completion(Thread.Failed{}, cache_space) == Thread.Discarded{} : Thread.Context}

law failed_context_unavailable:
  for cache_space: Bool
  for pool: List<&2, Thread.Context>
  for slot: Nat
  {Thread.available(Thread.finish(pool, slot, Thread.Failed{}, cache_space), slot) == False{} : Bool}

law successful_context_reusable:
  {Thread.completion(Thread.Succeeded{}, True{}) == Thread.Idle{} : Thread.Context}

# Converses: a lease is granted only from an Idle slot, and only a successful
# completion with cache space returns a context to Idle. C folds "not poisoned,
# heap within 32 MiB, fewer than eight cached" into these inputs (trusted).
law acquisition_requires_available:
  for pool: List<&2, Thread.Context>
  for slot: Nat
  for next: List<&2, Thread.Context>
  for acquired: {Thread.acquire(pool, slot) == Some{next} : Maybe<List<&2, Thread.Context>>}
  {Thread.available(pool, slot) == True{} : Bool}

law reusable_requires_success:
  for outcome: Thread.Outcome
  for cache_space: Bool
  for reusable: {Thread.completion(outcome, cache_space) == Thread.Idle{} : Thread.Context}
  {outcome == Thread.Succeeded{} : Thread.Outcome}

law reusable_requires_cache_space:
  for outcome: Thread.Outcome
  for cache_space: Bool
  for reusable: {Thread.completion(outcome, cache_space) == Thread.Idle{} : Thread.Context}
  {cache_space == True{} : Bool}
