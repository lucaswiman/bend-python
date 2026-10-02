// CPython and private Bend 2.0.28 runtime boundary. Every invocation exclusively
// owns a runtime instance; the generated runtime selects it through bp_current.
// The build relocates this block before the pinned runtime's first state use.
// BENDPY_RUNTIME_CONTEXT_BEGIN
#include <setjmp.h>
typedef struct BpRuntime {
  Corpus heap;
  u64 allocator[CUBE_T + 1][3 * ALC_WORDS] __attribute__((aligned(128)));
  u32 keep_words, cube_log, bank, workers;
  bool gpu;
  Stk stack;
  u64 heap_size;
  jmp_buf escape;
  bool poisoned;
  char error[256];
  struct BpRuntime* next;
} BpRuntime;
static _Thread_local BpRuntime* bp_current;
#define CORPUS (bp_current->heap)
#define ALC (bp_current->allocator)
#define KEEP_WORDS (bp_current->keep_words)
#define CUBE_LOG (bp_current->cube_log)
#define bank_lock (bp_current->bank)
#define pool_size (bp_current->workers)
#define io_gpu (bp_current->gpu)
#define io_stk (bp_current->stack)
#define corpus_size (bp_current->heap_size)
// BENDPY_RUNTIME_CONTEXT_END
// The build includes Python.h before the runtime's standard headers.
#include <Python.h>
#include <stdatomic.h>
#include <sys/random.h>
#include <time.h>

#if BANGS
#error "GPU entry points are not supported by this embedding."
#endif
#ifndef __STDC_IEC_559__
#error "F32 narrowing relies on IEEE 754 double-to-float conversion."
#endif

#if defined(CID(blas_scale)) || defined(CID(blas_dot)) || defined(CID(blas_axpy)) || defined(CID(blas_matmul))
#define BENDPY_HAS_BLAS 1
typedef struct {
  PyObject_HEAD
  PyObject* modules[4];
  PyObject* capsules[4];
  void* functions[4];
} BpBlasCache;
#endif

// An exported Bend function. Its fields are immutable after export, and its
// captureless Term holds no heap reference, so any runtime instance may run it.
typedef struct {
  PyObject_HEAD
  Term function;
  bool release_gil;
  PyObject* name;
  PyObject* module;
#ifdef BENDPY_HAS_BLAS
  BpBlasCache* blas;
#endif
} BpFunction;
typedef enum { BP_UNIT, BP_OBJECT, BP_U32, BP_F32, BP_BOOL, BP_STRING, BP_NAT, BP_BYTES,
  BP_F32_VIEW, BP_BLAS_DOT, BP_BLAS_AXPY, BP_BLAS_MATMUL } BpValue;
// A decoded Bend list: object handles, String codepoints, or raw U32 words.
typedef enum { BP_READ_OBJECTS, BP_READ_TEXT, BP_READ_WORDS } BpRead;
typedef enum { BP_INIT, BP_START, BP_RESUME, BP_DROP } BpOperation;

typedef struct {
  Py_buffer buffer;
  size_t size;
  bool live, writable, contiguous;
} BpView;

typedef struct {
  BpRuntime* runtime;
  PyObject** objects;
  size_t count, capacity, nargs;
  BpView* views;
  size_t view_count, view_capacity;
  const char* view_error;
  PyObject* view_exception;
  PyObject* module;
#ifdef BENDPY_HAS_BLAS
  BpBlasCache* blas;
  void* blas_functions[4];
#endif
  Term continuation, argument, function, map_step;
  size_t map_view, map_remaining;
  Term fields[5];
  u32 effect;
  u32* buffer;
  size_t length;
  BpValue result_kind;
  u64 result;
  u64 key;
  bool release_gil, pending, done, native_more;
  char error[256];
} BpCall;

static pthread_mutex_t bp_pool_mutex = PTHREAD_MUTEX_INITIALIZER;
static BpRuntime* bp_idle;
static size_t bp_idle_count;
static pthread_once_t bp_secret_once = PTHREAD_ONCE_INIT;
static u64 bp_secret;
static _Atomic u64 bp_call_counter;
#define BP_IDLE_LIMIT 8
#define BP_STACK_BYTES (1ull << 31)
#define BP_STACK_GUARD 16384
// Instances whose heap grew past this are unmapped rather than cached, so idle
// instances cannot pin a large call's resident pages until process exit.
#define BP_IDLE_HEAP_BYTES (32ull << 20)

static void bp_assert_attached(void) {
  // PyThreadState_Get fails fatally if this thread is detached, including on
  // free-threaded CPython. Traditional builds must also own the GIL.
  (void)PyThreadState_Get();
  if (bp_current != NULL) Py_FatalError("Python API reached inside Bend evaluation");
#ifndef Py_GIL_DISABLED
  if (!PyGILState_Check()) Py_FatalError("Bend bridge entered Python without the GIL");
#endif
}

static PyThreadState* bp_detach(BpCall* call) {
  bp_assert_attached();
#ifdef Py_GIL_DISABLED
  // Without a GIL, staying attached only delays stop-the-world pauses.
  return PyEval_SaveThread();
#else
  return call->release_gil ? PyEval_SaveThread() : NULL;
#endif
}

static void bendpy_panic(const char* message) {
  bp_current->poisoned = true;
  snprintf(bp_current->error, sizeof(bp_current->error), "%s", message);
  longjmp(bp_current->escape, 1);
}

static BpRuntime* bp_runtime_acquire(void) {
  bp_assert_attached();
  PyThreadState* thread = PyEval_SaveThread();
  pthread_mutex_lock(&bp_pool_mutex);
  BpRuntime* runtime = bp_idle;
  if (runtime) {
    bp_idle = runtime->next;
    --bp_idle_count;
    runtime->next = NULL;
  }
  pthread_mutex_unlock(&bp_pool_mutex);
  if (!runtime) {
    // The allocator cache has 128-byte alignment in the pinned runtime.
    void* memory = NULL;
    if (posix_memalign(&memory, _Alignof(BpRuntime), sizeof(BpRuntime)) == 0) {
      runtime = memory;
      memset(runtime, 0, sizeof(*runtime));
      runtime->cube_log = 7;
    }
  }
  PyEval_RestoreThread(thread);
  if (!runtime) PyErr_NoMemory();
  return runtime;
}

static bool bp_runtime_reusable(BpRuntime* runtime) {
  if (runtime->poisoned) return false;
  if (runtime->heap == NULL) return true;
  u64 pages = a32_load(a32_at(runtime->heap, H_BUMP));
  return (HEAP_OFF + (pages << PAGE_BITS)) * sizeof(u64) <= BP_IDLE_HEAP_BYTES;
}

static void bp_runtime_release(BpRuntime* runtime) {
  bp_assert_attached();
  PyThreadState* thread = PyEval_SaveThread();
  bool retained = false, reusable = bp_runtime_reusable(runtime);
  pthread_mutex_lock(&bp_pool_mutex);
  if (reusable && bp_idle_count < BP_IDLE_LIMIT) {
    runtime->next = bp_idle;
    bp_idle = runtime;
    ++bp_idle_count;
    retained = true;
  }
  pthread_mutex_unlock(&bp_pool_mutex);
  if (!retained) {
    if (runtime->heap) munmap(runtime->heap, runtime->heap_size);
    if (runtime->stack) munmap(runtime->stack, BP_STACK_BYTES + BP_STACK_GUARD);
    free(runtime);
  }
  PyEval_RestoreThread(thread);
}

static void* bp_alloc(size_t size) {
  void* p = malloc(size ? size : 1);
  if (p == NULL) bendpy_panic("unable to allocate Bend bridge buffer");
  return p;
}

static Term bp_apply(Env e, Term function, Term argument) {
  Loc at = task_node(e, FID_CLO_APPLY, TERM_HOLE, 0, 0);
  e.mem[at] = function;
  e.mem[at + 1] = argument;
  return corpus_eval(e.mem, term_tsk(FID_CLO_APPLY, at));
}

// Object wrappers may be packed or boxed on the heap. Anything else means the
// private runtime representation changed, so fail loudly rather than guess.
static u32 bp_unbox_as(Env e, Term object, u32 constructor) {
  if (term_aux(object) != constructor) bendpy_panic("unexpected bridge handle type");
  if (term_tag(object) == TAG_PAK) return (u32)term_loc(object);
  if (term_tag(object) != TAG_CTR) bendpy_panic("unexpected Python object representation");
  Term field;
  spare_free(e, 0, ctr_take(e, object, 1, &field));
  return (u32)field;
}

static u32 bp_unbox(Env e, Term object) {
  return bp_unbox_as(e, object, CID(PyObject));
}

static void bp_read_list(Env e, Term list, BpCall* call, BpRead mode) {
  bool string = mode == BP_READ_TEXT;
  size_t length = 0, capacity = 16;
  u32* data = bp_alloc(capacity * sizeof(u32));
  // Store before traversal so a runtime failure still frees the host buffer.
  call->buffer = data;
  u32 cons = string ? CID(SCon) : CID(Con), nil = string ? CID(SNil) : CID(Nil);
  while (term_tag(list) == TAG_CTR && term_aux(list) == cons) {
    Term fields[2];
    spare_free(e, 1, ctr_take(e, list, 2, fields));
    if (length == capacity) {
      capacity *= 2;
      u32* grown = realloc(data, capacity * sizeof(u32));
      if (grown == NULL) bendpy_panic("unable to grow Bend bridge buffer");
      data = grown;
      call->buffer = data;
    }
    if (mode == BP_READ_WORDS && (u64)fields[0] > UINT32_MAX)
      bendpy_panic("unexpected Bend U32 representation");
    data[length++] = mode == BP_READ_OBJECTS ? bp_unbox(e, fields[0]) : (u32)fields[0];
    list = fields[1];
  }
  if ((term_tag(list) != TAG_PAK && term_tag(list) != TAG_CTR) || term_aux(list) != nil)
    bendpy_panic("unexpected Bend list representation");
  term_drop(e, list);
  call->length = length;
}

// Handles are sealed: a Bend Object holds its arena index encrypted by a
// 32-bit Feistel permutation under a fresh per-invocation key. Bend code has
// no private constructors, so it could otherwise build PyObject{n} or do
// arithmetic on an id and silently alias another object of the same call.
// Accidental fabrication is usually detected, but 32-bit collisions remain
// possible. This is not authentication; the first rejected handle aborts the call.
static u64 bp_mix(u64 x) {
  x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ull;
  x ^= x >> 27; x *= 0x94d049bb133111ebull;
  return x ^ (x >> 31);
}

static void bp_seed_secret(void) {
  if (getrandom(&bp_secret, sizeof(bp_secret), 0) != sizeof(bp_secret))
    bp_secret = bp_mix((u64)time(NULL) ^ (u64)(uintptr_t)&bp_secret);
}

static u64 bp_call_key(void) {
  pthread_once(&bp_secret_once, bp_seed_secret);
  return bp_mix(bp_secret + bp_mix(atomic_fetch_add(&bp_call_counter, 1) + 1));
}

static u32 bp_round(u64 key, u32 round, u32 half) {
  return (u32)bp_mix(key ^ ((u64)round << 16 | half)) & 0xffff;
}

static u32 bp_seal(u64 key, u32 index) {
  u32 l = index >> 16, r = index & 0xffff;
  for (u32 i = 0; i < 4; ++i) { u32 t = l ^ bp_round(key, i, r); l = r; r = t; }
  return l << 16 | r;
}

static u32 bp_open(u64 key, u32 handle) {
  u32 l = handle >> 16, r = handle & 0xffff;
  for (u32 i = 4; i-- > 0;) { u32 t = r ^ bp_round(key, i, l); r = l; l = t; }
  return l << 16 | r;
}

static Term bp_object(BpCall* call, u64 index) {
  return term_pak(CID(PyObject), bp_seal(call->key, (u32)index));
}

static Term bp_pack(Env e, BpCall* call) {
  switch (call->result_kind) {
    case BP_UNIT: return term_pak(CID(Unit), 0);
    case BP_OBJECT: return bp_object(call, call->result);
#ifdef CID(F32View)
    case BP_F32_VIEW: return term_pak(CID(F32View), bp_seal(call->key ^ 0x66333276696577ull, (u32)call->result));
#endif
#ifdef BENDPY_HAS_BLAS
    case BP_BLAS_DOT:
      return io_tup(e, term_pak(CID(F32View), (u32)call->fields[0]),
        io_tup(e, term_pak(CID(F32View), (u32)call->fields[1]), (Term)call->result));
    case BP_BLAS_AXPY:
      return io_tup(e, term_pak(CID(F32View), (u32)call->fields[0]), term_pak(CID(F32View), (u32)call->fields[1]));
    case BP_BLAS_MATMUL:
      return io_tup(e, term_pak(CID(F32View), (u32)call->fields[0]),
        io_tup(e, term_pak(CID(F32View), (u32)call->fields[1]), term_pak(CID(F32View), (u32)call->fields[2])));
#endif
    case BP_BOOL: return term_pak(call->result ? CID(True) : CID(False), 0);
    case BP_STRING:
    case BP_BYTES: {
      bool bytes = call->result_kind == BP_BYTES;
      Term text = term_pak(bytes ? CID(Nil) : CID(SNil), 0);
      for (size_t i = call->length; i > 0; --i)
        text = io_node(e, bytes ? CID(Con) : CID(SCon), call->buffer[i - 1], text);
      return text;
    }
    default: return (Term)call->result;
  }
}

// Decode arguments in the exclusively leased runtime. The resulting handles
// and codepoints are plain C data; Python callbacks run outside evaluation.
static void bp_decode(Env e, BpCall* call) {
  Term* f = call->fields;
  switch (call->effect) {
#ifdef CID(export)
    case CID(export):
      bp_read_list(e, f[0], call, BP_READ_TEXT);
      if (term_tag(f[1]) != TAG_CLO || term_loc(f[1]) != 0)
        bendpy_panic("exports require a captureless function; use a top-level wrapper");
      f[2] = term_aux(f[2]) == CID(True);
      break;
#endif
#ifdef CID(require_no_kwargs)
    case CID(require_no_kwargs):
#endif
#ifdef CID(to_u32)
    case CID(to_u32):
#endif
#ifdef CID(to_f32)
    case CID(to_f32):
#endif
#ifdef CID(to_bool)
    case CID(to_bool):
#endif
#ifdef CID(to_string)
    case CID(to_string):
#endif
#ifdef CID(len)
    case CID(len):
#endif
#ifdef CID(truthy)
    case CID(truthy):
#endif
#ifdef CID(to_bytes)
    case CID(to_bytes):
#endif
      f[0] = bp_unbox(e, f[0]); break;
#ifdef CID(get_item)
    case CID(get_item):
      f[0] = bp_unbox(e, f[0]); f[1] = bp_unbox(e, f[1]); break;
#endif
#ifdef CID(set_item)
    case CID(set_item):
#endif
#ifdef CID(call)
    case CID(call):
#endif
      for (int i = 0; i < 3; ++i) f[i] = bp_unbox(e, f[i]); break;
#ifdef CID(getattr)
    case CID(getattr):
      f[0] = bp_unbox(e, f[0]); bp_read_list(e, f[1], call, BP_READ_TEXT); break;
#endif
#ifdef CID(builtins)
    case CID(builtins):
#endif
#ifdef CID(type_error)
    case CID(type_error):
#endif
#ifdef CID(from_string)
    case CID(from_string):
#endif
#ifdef CID(import_module)
    case CID(import_module):
#endif
      bp_read_list(e, f[0], call, BP_READ_TEXT); break;
#ifdef CID(tuple)
    case CID(tuple):
#endif
#ifdef CID(list)
    case CID(list):
#endif
      bp_read_list(e, f[0], call, BP_READ_OBJECTS); break;
#ifdef CID(from_bytes)
    case CID(from_bytes):
      bp_read_list(e, f[0], call, BP_READ_WORDS); break;
#endif
#ifdef CID(from_bool)
    case CID(from_bool): f[0] = term_aux(f[0]) == CID(True); break;
#endif
#ifdef CID(from_u32)
    case CID(from_u32): break;
#endif
#ifdef CID(from_f32)
    case CID(from_f32): break;
#endif
#ifdef CID(from_nat)
    case CID(from_nat):
      if ((u64)f[0] > NAT_IMM) bendpy_panic("unexpected Bend Nat representation");
      break;
#endif
#ifdef CID(none)
    case CID(none): break;
#endif
#ifdef CID(empty_dict)
    case CID(empty_dict): break;
#endif
#ifdef CID(borrow_f32)
    case CID(borrow_f32):
      f[0] = bp_unbox(e, f[0]); f[1] = term_aux(f[1]) == CID(True); break;
#endif
#ifdef CID(f32_size)
    case CID(f32_size):
#endif
#ifdef CID(f32_shape)
    case CID(f32_shape):
#endif
#ifdef CID(f32_read)
    case CID(f32_read):
#endif
#ifdef CID(f32_write)
    case CID(f32_write):
#endif
#ifdef CID(f32_release)
    case CID(f32_release):
#endif
#ifdef CID(f32_map)
    case CID(f32_map):
#endif
#if defined(CID(f32_size)) || defined(CID(f32_shape)) || defined(CID(f32_read)) || defined(CID(f32_write)) || defined(CID(f32_release)) || defined(CID(f32_map))
      f[0] = bp_unbox_as(e, f[0], CID(F32View)); break;
#endif
#ifdef CID(f32_modify)
    case CID(f32_modify):
      f[0] = bp_unbox_as(e, f[0], CID(F32View));
      if (term_tag(f[2]) != TAG_CLO || term_loc(f[2]) != 0)
        bendpy_panic("float32 modifiers require a captureless function; use a template");
      break;
#endif
#ifdef CID(blas_scale)
    case CID(blas_scale): f[0] = bp_unbox_as(e, f[0], CID(F32View)); break;
#endif
#ifdef CID(blas_dot)
    case CID(blas_dot):
#endif
#ifdef CID(blas_axpy)
    case CID(blas_axpy):
#endif
#if defined(CID(blas_dot)) || defined(CID(blas_axpy))
      f[0] = bp_unbox_as(e, f[0], CID(F32View)); f[1] = bp_unbox_as(e, f[1], CID(F32View)); break;
#endif
#ifdef CID(blas_matmul)
    case CID(blas_matmul):
      for (int i = 0; i < 3; ++i) f[i] = bp_unbox_as(e, f[i], CID(F32View)); break;
#endif
    default: bendpy_panic("unsupported foreign effect in Python extension");
  }
}

// Native buffer operations touch retained storage and plain C metadata only.
// Errors are raised after leaving the runtime and reattaching to Python.
static BpView* bp_view(BpCall* call, u64 handle) {
  u32 index = bp_open(call->key ^ 0x66333276696577ull, (u32)handle);
  if (handle > UINT32_MAX || index >= call->view_count || !call->views[index].live) {
    call->view_error = "invalid or released float32 view";
    call->view_exception = PyExc_ValueError;
    return NULL;
  }
  return &call->views[index];
}

// Only checked indexed effects and the count-bounded map cursor call this.
static char* bp_view_address(BpView* view, u64 index) {
  if (view->contiguous) return (char*)view->buffer.buf + index * sizeof(float);
  Py_ssize_t offset = 0;
  for (int axis = view->buffer.ndim; axis-- > 0;) {
    size_t dimension = (size_t)view->buffer.shape[axis];
    offset += (Py_ssize_t)(index % dimension) * view->buffer.strides[axis];
    index /= dimension;
  }
  return (char*)view->buffer.buf + offset;
}

static char* bp_view_at(BpCall* call, BpView* view, u64 index) {
  if (index >= view->size) {
    call->view_error = "float32 view index out of bounds";
    call->view_exception = PyExc_IndexError;
    return NULL;
  }
  return bp_view_address(view, index);
}

static bool bp_writable(BpCall* call, BpView* view) {
  if (view->writable) return true;
  call->view_error = "float32 view was borrowed read-only";
  call->view_exception = PyExc_BufferError;
  return false;
}

static void bp_store(char* address, u32 bits) {
  memcpy(address, &bits, sizeof(bits));
}

#ifdef CID(f32_map)
static bool bp_map_valid(BpCall* call) {
  Term step = call->map_step;
  bool valid = call->map_remaining ? term_tag(step) == TAG_CLO :
    ((term_tag(step) == TAG_PAK || term_tag(step) == TAG_CTR) && term_aux(step) == CID(Unit));
  if (!valid) {
    call->view_error = "invalid float32 map program";
    call->view_exception = PyExc_RuntimeError;
  }
  return valid;
}

// Safe dependent programs supply exactly this many single-use affine steps.
// The native counter also bounds unsafe short/long programs independently.
static void bp_map_next(Env e, BpCall* call) {
  Term step = call->map_step;
  call->map_step = 0;
  if (call->map_remaining == 0) {
    term_drop(e, step);
    call->argument = term_pak(CID(F32View), bp_seal(call->key ^ 0x66333276696577ull, call->map_view));
    return;
  }
  BpView* view = &call->views[call->map_view];
  char* address = bp_view_address(view, view->size - call->map_remaining);
  u32 bits;
  memcpy(&bits, address, sizeof(bits));
  Term result = bp_apply(e, step, (Term)bits);
  if (term_tag(result) != TAG_CTR || term_aux(result) != CID(Tuple)) {
    call->map_step = result;
    call->view_error = "invalid float32 map result";
    call->view_exception = PyExc_RuntimeError;
    return;
  }
  Term fields[2];
  spare_free(e, 1, ctr_take(e, result, 2, fields));
  call->map_step = fields[1];
  --call->map_remaining;
  // Validate the successor before committing this cell, including at count 0.
  if (!bp_map_valid(call)) return;
  bp_store(address, (u32)fields[0]);
}
#endif

static bool bp_memory_effect(Env e, BpCall* call) {
  switch (call->effect) {
#ifdef CID(f32_size)
    case CID(f32_size):
#endif
#ifdef CID(f32_shape)
    case CID(f32_shape):
#endif
#ifdef CID(f32_read)
    case CID(f32_read):
#endif
#ifdef CID(f32_write)
    case CID(f32_write):
#endif
#ifdef CID(f32_modify)
    case CID(f32_modify):
#endif
#ifdef CID(f32_map)
    case CID(f32_map):
#endif
#if defined(CID(f32_size)) || defined(CID(f32_shape)) || defined(CID(f32_read)) || defined(CID(f32_write)) || defined(CID(f32_modify)) || defined(CID(f32_map))
    {
#ifdef CID(f32_map)
      if (call->effect == CID(f32_map)) {
        call->map_step = call->fields[1];
        call->argument = 0;
      }
#endif
      BpView* view = bp_view(call, call->fields[0]);
      if (!view) return true;
      Term handle = term_pak(CID(F32View), (u32)call->fields[0]);
#ifdef CID(f32_map)
      if (call->effect == CID(f32_map)) {
        if (!bp_writable(call, view)) return true;
        if (term_tag(call->map_step) != TAG_CLO) {
          call->view_error = "invalid float32 map producer";
          call->view_exception = PyExc_RuntimeError;
          return true;
        }
        call->map_view = (size_t)(view - call->views);
        call->map_remaining = view->size;
        Term producer = call->map_step;
        call->map_step = 0;
        call->map_step = bp_apply(e, producer, (Term)view->size);
        bp_map_valid(call);
        return true;
      }
#endif
#ifdef CID(f32_size)
      if (call->effect == CID(f32_size)) {
        call->argument = io_tup(e, handle, (Term)view->size); return true;
      }
#endif
#ifdef CID(f32_shape)
      if (call->effect == CID(f32_shape)) {
        Term shape = term_pak(CID(Nil), 0);
        for (int axis = view->buffer.ndim; axis-- > 0;)
          shape = io_node(e, CID(Con), (Term)view->buffer.shape[axis], shape);
        call->argument = io_tup(e, handle, shape); return true;
      }
#endif
      char* address = bp_view_at(call, view, call->fields[1]);
      if (!address) return true;
      u32 bits;
#ifdef CID(f32_read)
      if (call->effect == CID(f32_read)) {
        memcpy(&bits, address, sizeof(bits));
        call->argument = io_tup(e, handle, (Term)bits); return true;
      }
#endif
      if (!bp_writable(call, view)) return true;
#ifdef CID(f32_modify)
      if (call->effect == CID(f32_modify)) {
        memcpy(&bits, address, sizeof(bits));
        bits = (u32)bp_apply(e, call->fields[2], (Term)bits);
      } else
#endif
      {
        bits = (u32)call->fields[2];
      }
      bp_store(address, bits);
      call->argument = handle; return true;
    }
#endif
    default: return false;
  }
}

static bool bp_native(BpCall* call, BpOperation operation) {
  PyThreadState* thread = bp_detach(call);
  bp_current = call->runtime;
  // Poisoned instances are never cached, so every lease starts healthy.
  if (setjmp(bp_current->escape)) {
    snprintf(call->error, sizeof(call->error), "%s", bp_current->error);
  } else {
    if (CORPUS == NULL) {
      corpus_setup(false, 1, 0);
      io_stk = pool_mmap(BP_STACK_BYTES + BP_STACK_GUARD);
      if (mprotect((char*)io_stk + BP_STACK_BYTES, BP_STACK_GUARD, PROT_NONE))
        bendpy_panic("unable to guard Bend stack");
    }
    Env e = { CORPUS, ALC[0] };
    switch (operation) {
      case BP_INIT:
        call->continuation = corpus_eval(CORPUS,
          term_tsk(MAIN_FID, task_node(e, MAIN_FID, TERM_HOLE, 0, 0)));
        call->argument = term_clo(FID_IO_EMIT, 0);
        break;
      case BP_START: {
        Term args = term_pak(CID(Nil), 0);
        for (size_t i = call->nargs; i > 0; --i)
          args = io_node(e, CID(Con), bp_object(call, i - 1), args);
        Term input = io_node(e, CID(PyCall), args, bp_object(call, call->nargs));
        call->continuation = bp_apply(e, call->function, input);
        call->argument = term_clo(FID_IO_EMIT, 0);
        break;
      }
      case BP_RESUME: {
        call->native_more = false;
        // Check signals between batches without attaching for every element.
        for (size_t operations = 0;; ++operations) {
#ifdef CID(f32_map)
          if (call->map_step) {
            bp_map_next(e, call);
            if (call->view_error) break;
          } else
#endif
          {
            if (call->pending) {
              call->argument = bp_pack(e, call);
              call->pending = false;
              free(call->buffer); call->buffer = NULL;
            }
            Term request = bp_apply(e, call->continuation, call->argument);
            call->continuation = 0;
            call->effect = (u32)term_aux(request);
            if (call->effect == CID(Emit)) {
              Term value;
              spare_free(e, 0, ctr_take(e, request, 1, &value));
              if (call->module) term_drop(e, value);
              else call->result = bp_unbox(e, value);
              call->done = true;
              break;
            }
            u32 n = cid_arity(call->effect);
            if (n == 0 || n > 5) bendpy_panic("unsupported foreign effect arity");
            spare_free(e, cls_fit(n), ctr_take(e, request, n, call->fields));
            call->continuation = call->fields[n - 1];
            bp_decode(e, call);
            if (!bp_memory_effect(e, call) || call->view_error) break;
          }
          if (operations == 4095) { call->native_more = true; break; }
        }
        break;
      }
      case BP_DROP:
        if (call->map_step) { term_drop(e, call->map_step); call->map_step = 0; }
        if (call->native_more) {
          term_drop(e, call->argument); call->native_more = false;
        }
        if (call->continuation) term_drop(e, call->continuation);
        break;
    }
  }
  bp_current = NULL;
  if (thread) PyEval_RestoreThread(thread);
  bp_assert_attached();
  return call->error[0] == 0 && call->view_error == NULL;
}

// Each invocation owns a reference arena. Handles never contain PyObject*
// addresses. Open and check them before access, and release the arena outside
// evaluation.
static PyObject* bp_get(BpCall* call, u64 handle) {
  bp_assert_attached();
  u32 index = bp_open(call->key, (u32)handle);
  if (index >= call->count) {
    PyErr_SetString(PyExc_ValueError,
      "invalid Python object handle: handles must come from the bridge in this call");
    return NULL;
  }
  return call->objects[index];
}

static bool bp_add(BpCall* call, PyObject* object) {
  bp_assert_attached();
  if (object == NULL) return false;
  if (call->count == UINT32_MAX) {
    Py_DECREF(object); PyErr_SetString(PyExc_OverflowError, "too many object handles"); return false;
  }
  if (call->count == call->capacity) {
    size_t capacity = call->capacity ? call->capacity * 2 : 16;
    PyObject** objects = PyMem_Realloc(call->objects, capacity * sizeof(PyObject*));
    if (objects == NULL) { Py_DECREF(object); PyErr_NoMemory(); return false; }
    call->objects = objects; call->capacity = capacity;
  }
  call->result_kind = BP_OBJECT;
  call->result = call->count;
  call->objects[call->count++] = object;
  return true;
}

static PyObject* bp_text(BpCall* call) {
  bp_assert_attached();
  for (size_t i = 0; i < call->length; ++i) {
    if (call->buffer[i] > 0x10ffff) {
      PyErr_SetString(PyExc_ValueError, "Bend String contains an invalid Unicode codepoint");
      return NULL;
    }
  }
  return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, call->buffer, call->length);
}

static PyObject* bp_call_python(PyObject*, PyObject*, PyObject*);

#ifdef BENDPY_HAS_BLAS
static int bp_blas_traverse(PyObject* self, visitproc visit, void* arg) {
  BpBlasCache* cache = (BpBlasCache*)self;
  for (int i = 0; i < 4; ++i) {
    Py_VISIT(cache->modules[i]); Py_VISIT(cache->capsules[i]);
  }
  return 0;
}

static int bp_blas_clear(PyObject* self) {
  BpBlasCache* cache = (BpBlasCache*)self;
  memset(cache->functions, 0, sizeof(cache->functions));
  for (int i = 0; i < 4; ++i) {
    Py_CLEAR(cache->modules[i]); Py_CLEAR(cache->capsules[i]);
  }
  return 0;
}

static void bp_blas_dealloc(PyObject* self) {
  PyObject_GC_UnTrack(self);
  bp_blas_clear(self);
  PyObject_GC_Del(self);
}

static PyTypeObject bp_blas_type = {
  PyVarObject_HEAD_INIT(NULL, 0)
  .tp_name = "bend_python.blas_cache",
  .tp_basicsize = sizeof(BpBlasCache),
  .tp_dealloc = bp_blas_dealloc,
  .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_DISALLOW_INSTANTIATION,
  .tp_traverse = bp_blas_traverse,
  .tp_clear = bp_blas_clear,
};
#endif

static int bp_function_traverse(PyObject* self, visitproc visit, void* arg) {
  BpFunction* function = (BpFunction*)self;
  Py_VISIT(function->name); Py_VISIT(function->module);
#ifdef BENDPY_HAS_BLAS
  Py_VISIT(function->blas);
#endif
  return 0;
}

static int bp_function_clear(PyObject* self) {
  BpFunction* function = (BpFunction*)self;
  Py_CLEAR(function->name); Py_CLEAR(function->module);
#ifdef BENDPY_HAS_BLAS
  Py_CLEAR(function->blas);
#endif
  return 0;
}

static void bp_function_dealloc(PyObject* self) {
  PyObject_GC_UnTrack(self);
  bp_function_clear(self);
  PyObject_GC_Del(self);
}

static PyObject* bp_function_name(PyObject* self, void* closure) {
  return Py_NewRef(((BpFunction*)self)->name);
}

static PyObject* bp_function_module(PyObject* self, void* closure) {
  return Py_NewRef(((BpFunction*)self)->module);
}

static PyObject* bp_function_repr(PyObject* self) {
  BpFunction* function = (BpFunction*)self;
  return PyUnicode_FromFormat("<bend function %U.%U>", function->module, function->name);
}

// Pickle by reference, like a module-level function: module.name.
static PyObject* bp_function_reduce(PyObject* self, PyObject* unused) {
  return Py_NewRef(((BpFunction*)self)->name);
}

static PyGetSetDef bp_function_getset[] = {
  {"__name__", bp_function_name, NULL, NULL, NULL},
  {"__qualname__", bp_function_name, NULL, NULL, NULL},
  {"__module__", bp_function_module, NULL, NULL, NULL},
  {NULL},
};

static PyMethodDef bp_function_methods[] = {
  {"__reduce__", bp_function_reduce, METH_NOARGS, NULL},
  {NULL},
};

static PyTypeObject bp_function_type = {
  PyVarObject_HEAD_INIT(NULL, 0)
  .tp_name = "bend_python.function",
  .tp_basicsize = sizeof(BpFunction),
  .tp_dealloc = bp_function_dealloc,
  .tp_repr = bp_function_repr,
  .tp_call = bp_call_python,
  .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION | Py_TPFLAGS_HAVE_GC,
  .tp_traverse = bp_function_traverse,
  .tp_clear = bp_function_clear,
  .tp_doc = "A native Bend function.",
  .tp_methods = bp_function_methods,
  .tp_getset = bp_function_getset,
};

static bool bp_export(BpCall* call) {
  if (!call->module) { PyErr_SetString(PyExc_RuntimeError, "exports are only allowed during initialization"); return false; }
  PyObject* name = bp_text(call);
  if (name == NULL) return false;
  Py_ssize_t size;
  const char* text = PyUnicode_AsUTF8AndSize(name, &size);
  if (!text) { Py_DECREF(name); return false; }
  if (size == 0 || strlen(text) != (size_t)size || PyObject_HasAttr(call->module, name)) {
    Py_DECREF(name); PyErr_SetString(PyExc_ValueError, "empty, duplicate, or NUL-containing export name"); return false;
  }
  PyObject* module_name = PyModule_GetNameObject(call->module);
  BpFunction* function = module_name ? PyObject_GC_New(BpFunction, &bp_function_type) : NULL;
  if (!function) { Py_DECREF(name); Py_XDECREF(module_name); return false; }
  function->function = call->fields[1]; function->release_gil = call->fields[2];
  function->name = name; function->module = module_name;
#ifdef BENDPY_HAS_BLAS
  function->blas = (BpBlasCache*)Py_NewRef((PyObject*)call->blas);
#endif
  PyObject_GC_Track(function);
  int added = PyModule_AddObjectRef(call->module, text, (PyObject*)function);
  Py_DECREF(function);
  return added == 0;
}

#ifdef CID(borrow_f32)
static bool bp_borrow_f32(BpCall* call, PyObject* object, bool writable) {
  Py_buffer buffer;
  int flags = PyBUF_STRIDES | PyBUF_FORMAT | (writable ? PyBUF_WRITABLE : 0);
  if (PyObject_GetBuffer(object, &buffer, flags) < 0) return false;
  const char* format = buffer.format;
  const char* error = NULL;
  if (!format || buffer.itemsize != sizeof(float) ||
      !(strcmp(format, "f") == 0 || strcmp(format, "@f") == 0 ||
        strcmp(format, "=f") == 0 || strcmp(format, "<f") == 0))
    error = "expected native float32 storage; no implicit casts or copies";
  if (buffer.ndim < 0 || buffer.ndim > PyBUF_MAX_NDIM || buffer.len < 0 ||
      buffer.len % sizeof(float) || (u64)(buffer.len / sizeof(float)) > NAT_IMM ||
      (buffer.ndim > 0 && !buffer.shape))
    error = "unsupported float32 buffer dimensions or length";
  // A buffer export describes valid storage; its span must also fit host address
  // arithmetic. Exotic overlapping layouts are accepted only for read access.
  Py_ssize_t low = 0, high = 0;
  size_t product = 1;
  struct { size_t stride, dimension; } axes[PyBUF_MAX_NDIM];
  int count = 0;
  if (!error) {
    for (int axis = 0; axis < buffer.ndim; ++axis) {
      Py_ssize_t dimension = buffer.shape[axis], stride = buffer.strides ? buffer.strides[axis] : 0, span;
      if (dimension < 0 || (u64)dimension > NAT_IMM) { error = "unsupported float32 dimension"; break; }
      if (__builtin_mul_overflow(product, (size_t)dimension, &product)) {
        error = "float32 element count overflow"; break;
      }
      if (dimension == 0 || !buffer.strides) continue;
      if (__builtin_mul_overflow(dimension - 1, stride, &span) ||
          (span < 0 ? __builtin_add_overflow(low, span, &low) : __builtin_add_overflow(high, span, &high))) {
        error = "float32 stride offset overflow"; break;
      }
      if (writable && dimension > 1) {
        size_t absolute = stride < 0 ? (size_t)(-(stride + 1)) + 1 : (size_t)stride;
        int at = count++;
        while (at > 0 && axes[at - 1].stride > absolute) { axes[at] = axes[at - 1]; --at; }
        axes[at].stride = absolute; axes[at].dimension = (size_t)dimension;
      }
    }
    if (!error && high > PY_SSIZE_T_MAX - (Py_ssize_t)sizeof(float))
      error = "float32 storage span overflow";
    if (!error && (product != (size_t)buffer.len / sizeof(float) || (product && !buffer.buf)))
      error = "inconsistent float32 buffer shape";
    if (!error && writable && product && buffer.strides) {
      size_t span = sizeof(float);
      for (int axis = 0; axis < count; ++axis) {
        size_t extra;
        if (axes[axis].stride < span) { error = "writable float32 view must not overlap"; break; }
        if (__builtin_mul_overflow(axes[axis].dimension - 1, axes[axis].stride, &extra) ||
            __builtin_add_overflow(span, extra, &span) || span > PY_SSIZE_T_MAX) {
          error = "float32 storage span overflow"; break;
        }
      }
    }
  }
  if (error) { PyBuffer_Release(&buffer); PyErr_SetString(PyExc_BufferError, error); return false; }
  if (call->view_count == UINT32_MAX) {
    PyBuffer_Release(&buffer); PyErr_SetString(PyExc_OverflowError, "too many float32 views"); return false;
  }
  if (call->view_count == call->view_capacity) {
    size_t capacity = call->view_capacity ? call->view_capacity * 2 : 4;
    BpView* views = PyMem_Realloc(call->views, capacity * sizeof(BpView));
    if (!views) { PyBuffer_Release(&buffer); PyErr_NoMemory(); return false; }
    call->views = views; call->view_capacity = capacity;
  }
  call->views[call->view_count] = (BpView){ .buffer = buffer, .size = product,
    .live = true, .writable = writable, .contiguous = !buffer.strides || PyBuffer_IsContiguous(&buffer, 'C') };
  call->result_kind = BP_F32_VIEW; call->result = call->view_count++;
  return true;
}
#endif

#ifdef BENDPY_HAS_BLAS
// SciPy's public Cython BLAS API wraps the provider's Fortran ABI. Keep the
// first successfully validated capsule for each operation for the lifetime of
// the module's exports. Failed resolutions remain retryable.
static bool bp_blas_load(BpCall* call, int operation) {
  static const char* names[] = {"sscal", "sdot", "saxpy", "sgemm"};
  #define BP_SCIPY_FLOAT "__pyx_t_5scipy_6linalg_11cython_blas_s *"
  static const char* signatures[] = {
    "void (int *, " BP_SCIPY_FLOAT ", " BP_SCIPY_FLOAT ", int *)",
    "__pyx_t_5scipy_6linalg_11cython_blas_s (int *, " BP_SCIPY_FLOAT ", int *, " BP_SCIPY_FLOAT ", int *)",
    "void (int *, " BP_SCIPY_FLOAT ", " BP_SCIPY_FLOAT ", int *, " BP_SCIPY_FLOAT ", int *)",
    "void (char *, char *, int *, int *, int *, " BP_SCIPY_FLOAT ", " BP_SCIPY_FLOAT ", int *, " BP_SCIPY_FLOAT ", int *, " BP_SCIPY_FLOAT ", " BP_SCIPY_FLOAT ", int *)"
  };
  #undef BP_SCIPY_FLOAT
  if (call->blas_functions[operation]) return true;
  BpBlasCache* cache = call->blas;
#ifdef Py_GIL_DISABLED
  Py_BEGIN_CRITICAL_SECTION(cache);
#endif
  call->blas_functions[operation] = cache->functions[operation];
#ifdef Py_GIL_DISABLED
  Py_END_CRITICAL_SECTION();
#endif
  if (call->blas_functions[operation]) return true;
  // Imports and attribute access can reenter; resolve outside the cache lock.
  PyObject* module = PyImport_ImportModule("scipy.linalg.cython_blas");
  if (!module) return false;
  PyObject* api = PyObject_GetAttrString(module, "__pyx_capi__");
  PyObject* capsule = api ? PyMapping_GetItemString(api, names[operation]) : NULL;
  Py_XDECREF(api);
  if (!capsule) { Py_DECREF(module); return false; }
  const char* signature = PyCapsule_CheckExact(capsule) ? PyCapsule_GetName(capsule) : NULL;
  if (!signature || strcmp(signature, signatures[operation]) != 0) {
    Py_DECREF(capsule); Py_DECREF(module);
    PyErr_SetString(PyExc_ImportError, "unsupported SciPy BLAS capsule ABI; an LP64 SciPy build is required");
    return false;
  }
  void* function = PyCapsule_GetPointer(capsule, signatures[operation]);
  if (!function) { Py_DECREF(capsule); Py_DECREF(module); return false; }
#ifdef Py_GIL_DISABLED
  Py_BEGIN_CRITICAL_SECTION(cache);
#endif
  if (!cache->functions[operation]) {
    cache->modules[operation] = module; module = NULL;
    cache->capsules[operation] = capsule; capsule = NULL;
    cache->functions[operation] = function;
  }
  call->blas_functions[operation] = cache->functions[operation];
#ifdef Py_GIL_DISABLED
  Py_END_CRITICAL_SECTION();
#endif
  Py_XDECREF(capsule); Py_XDECREF(module);
  return true;
}

static bool bp_blas_int(Py_ssize_t value, int* result) {
  if (value < 0 || value > INT_MAX) {
    PyErr_SetString(PyExc_OverflowError, "BLAS dimension, increment, or leading dimension exceeds LP64 range");
    return false;
  }
  *result = (int)value;
  return true;
}

// Only positive aligned strides enter BLAS. Empty storage has no address or
// layout requirement; give its unused native increment/leading dimension 1.
static bool bp_blas_layout(BpView* view, bool matrix, int* rows, int* cols, int* step) {
  Py_buffer* b = &view->buffer;
  if (b->ndim != (matrix ? 2 : 1)) {
    PyErr_SetString(PyExc_BufferError, "BLAS expects a vector or matrix of the specified rank");
    return false;
  }
  if (!bp_blas_int(b->shape[0], rows) || (matrix && !bp_blas_int(b->shape[1], cols))) return false;
  *step = 1;
  if (!view->size) return true;
  if ((uintptr_t)b->buf % _Alignof(float)) {
    PyErr_SetString(PyExc_BufferError, "BLAS requires aligned float32 storage"); return false;
  }
  Py_ssize_t stride = b->strides ? b->strides[matrix ? 1 : 0] : sizeof(float);
  if (matrix) {
    Py_ssize_t row_stride = b->strides ? b->strides[0] : (Py_ssize_t)*cols * sizeof(float);
    if (*rows > 1 && row_stride != sizeof(float)) {
      PyErr_SetString(PyExc_BufferError, "BLAS matrices require Fortran layout; no implicit copies"); return false;
    }
    if (*cols == 1) stride = (Py_ssize_t)*rows * sizeof(float);
  }
  if (stride <= 0 || stride % sizeof(float) || (matrix && stride / (Py_ssize_t)sizeof(float) < *rows)) {
    PyErr_SetString(PyExc_BufferError, "BLAS requires positive float32 strides and valid leading dimensions");
    return false;
  }
  return bp_blas_int(stride / sizeof(float), step);
}

// Conservatively reject intersecting storage spans, including gaps between
// strided elements. BLAS does not guarantee results for aliased destinations.
static bool bp_blas_disjoint(BpView* left, int lr, int lc, int ls,
                             BpView* right, int rr, int rc, int rs, bool matrix) {
  if (!left->size || !right->size) return true;
  uintptr_t l = (uintptr_t)left->buffer.buf, r = (uintptr_t)right->buffer.buf;
  size_t ln = matrix ? (size_t)(lc - 1) * ls + lr : (size_t)(lr - 1) * ls + 1;
  size_t rn = matrix ? (size_t)(rc - 1) * rs + rr : (size_t)(rr - 1) * rs + 1;
  uintptr_t le, re;
  if (__builtin_add_overflow(l, ln * sizeof(float), &le) ||
      __builtin_add_overflow(r, rn * sizeof(float), &re)) {
    PyErr_SetString(PyExc_OverflowError, "BLAS storage address overflow"); return false;
  }
  if (l < re && r < le) {
    PyErr_SetString(PyExc_BufferError, "BLAS destination must not overlap an input storage span"); return false;
  }
  return true;
}

static bool bp_blas(BpCall* call, int operation) {
  int count = operation == 0 ? 1 : operation == 3 ? 3 : 2;
  BpView* views[3];
  int rows[3] = {0}, cols[3] = {0}, steps[3] = {0};
  bool matrix = operation == 3;
  for (int i = 0; i < count; ++i) {
    views[i] = bp_view(call, call->fields[i]);
    if (!views[i]) { PyErr_SetString(call->view_exception, call->view_error); return false; }
    if (!bp_blas_layout(views[i], matrix, &rows[i], &cols[i], &steps[i])) return false;
  }
  int destination = operation == 0 ? 0 : operation == 2 ? 1 : 2;
  if (operation != 1 && !bp_writable(call, views[destination])) {
    PyErr_SetString(call->view_exception, call->view_error); return false;
  }
  if ((operation == 1 || operation == 2) && rows[0] != rows[1]) {
    PyErr_SetString(PyExc_BufferError, "BLAS vector lengths must match"); return false;
  }
  if (matrix && (cols[0] != rows[1] || rows[0] != rows[2] || cols[1] != cols[2])) {
    PyErr_SetString(PyExc_BufferError, "BLAS matrix dimensions must match"); return false;
  }
  if (operation == 2 && !bp_blas_disjoint(views[0], rows[0], 0, steps[0], views[1], rows[1], 0, steps[1], false)) return false;
  if (matrix) {
    for (int i = 0; i < 2; ++i)
      if (!bp_blas_disjoint(views[i], rows[i], cols[i], steps[i], views[2], rows[2], cols[2], steps[2], true)) return false;
  }
  if (!bp_blas_load(call, operation)) return false;
  float alpha = 1.0f, beta = 0.0f, value = 0.0f;
  if (operation == 0 || operation == 2) {
    u32 bits = (u32)call->fields[operation == 0 ? 1 : 2];
    memcpy(&alpha, &bits, sizeof(alpha));
  }
  // Every pointer is backed by a retained Py_buffer. No Python/runtime API is
  // called while detached; callers synchronize concurrent access to storage.
  PyThreadState* state = bp_detach(call);
  if (operation == 0 && rows[0]) {
    typedef void (*Scale)(int*, float*, float*, int*);
    ((Scale)call->blas_functions[0])(&rows[0], &alpha, views[0]->buffer.buf, &steps[0]);
  } else if (operation == 1 && rows[0]) {
    typedef float (*Dot)(int*, float*, int*, float*, int*);
    value = ((Dot)call->blas_functions[1])(&rows[0], views[0]->buffer.buf, &steps[0], views[1]->buffer.buf, &steps[1]);
  } else if (operation == 2 && rows[0]) {
    typedef void (*Axpy)(int*, float*, float*, int*, float*, int*);
    ((Axpy)call->blas_functions[2])(&rows[0], &alpha, views[0]->buffer.buf, &steps[0], views[1]->buffer.buf, &steps[1]);
  } else if (matrix && rows[2] && cols[2]) {
    if (cols[0]) {
      typedef void (*Matmul)(char*, char*, int*, int*, int*, float*, float*, int*, float*, int*, float*, float*, int*);
      char no = 'N';
      ((Matmul)call->blas_functions[3])(&no, &no, &rows[2], &cols[2], &cols[0], &alpha,
        views[0]->buffer.buf, &steps[0], views[1]->buffer.buf, &steps[1], &beta, views[2]->buffer.buf, &steps[2]);
    } else {
      // K=0 must clear C, even if an implementation's quick return skips it.
      float* out = views[2]->buffer.buf;
      for (int j = 0; j < cols[2]; ++j)
        for (int i = 0; i < rows[2]; ++i) out[(size_t)j * steps[2] + i] = 0.0f;
    }
  }
  if (state) PyEval_RestoreThread(state);
  if (PyErr_CheckSignals() < 0) return false;
  if (operation == 0) {
    call->result_kind = BP_F32_VIEW; call->result = (size_t)(views[0] - call->views);
  } else if (operation == 1) {
    call->result_kind = BP_BLAS_DOT;
    u32 bits; memcpy(&bits, &value, sizeof(bits)); call->result = bits;
  } else call->result_kind = matrix ? BP_BLAS_MATMUL : BP_BLAS_AXPY;
  return true;
}
#endif


static bool bp_effect(BpCall* call) {
  bp_assert_attached();
  Term* f = call->fields;
  PyObject *a = NULL, *b = NULL, *c = NULL, *result = NULL;
  call->result_kind = BP_UNIT;
  switch (call->effect) {
#ifdef CID(blas_scale)
    case CID(blas_scale): return bp_blas(call, 0);
#endif
#ifdef CID(blas_dot)
    case CID(blas_dot): return bp_blas(call, 1);
#endif
#ifdef CID(blas_axpy)
    case CID(blas_axpy): return bp_blas(call, 2);
#endif
#ifdef CID(blas_matmul)
    case CID(blas_matmul): return bp_blas(call, 3);
#endif
#ifdef CID(export)
    case CID(export): return bp_export(call);
#endif
#ifdef CID(borrow_f32)
    case CID(borrow_f32):
      a = bp_get(call, f[0]); return a && bp_borrow_f32(call, a, f[1] != 0);
#endif
#ifdef CID(f32_release)
    case CID(f32_release): {
      BpView* view = bp_view(call, f[0]);
      if (!view) { PyErr_SetString(call->view_exception, call->view_error); return false; }
      view->live = false; PyBuffer_Release(&view->buffer); return true;
    }
#endif
#ifdef CID(require_no_kwargs)
    case CID(require_no_kwargs):
      a = bp_get(call, f[0]);
      if (!a) return false;
      if (!PyDict_Check(a) || PyDict_Size(a) != 0) {
        PyErr_SetString(PyExc_TypeError, "this function accepts positional arguments only"); return false;
      }
      return true;
#endif
#ifdef CID(to_u32)
    case CID(to_u32): {
      a = bp_get(call, f[0]); if (!a) return false;
      if (!PyLong_Check(a) || PyBool_Check(a)) {
        PyErr_SetString(PyExc_TypeError, "expected an integer in the U32 range"); return false;
      }
      unsigned long x = PyLong_AsUnsignedLong(a);
      if (PyErr_Occurred()) return false;
      if (x > UINT32_MAX) { PyErr_SetString(PyExc_OverflowError, "integer does not fit U32"); return false; }
      call->result_kind = BP_U32; call->result = x; return true;
    }
#endif
#ifdef CID(from_u32)
    case CID(from_u32): return bp_add(call, PyLong_FromUnsignedLong((u32)f[0]));
#endif
#ifdef CID(from_nat)
    case CID(from_nat): return bp_add(call, PyLong_FromUnsignedLongLong((u64)f[0]));
#endif
#ifdef CID(to_f32)
    case CID(to_f32): {
      a = bp_get(call, f[0]); if (!a) return false;
      if (!PyFloat_Check(a)) { PyErr_SetString(PyExc_TypeError, "expected a float"); return false; }
      // IEEE 754 narrowing (Annex F): round to nearest, overflowing to infinity.
      union { float f; u32 u; } x = { .f = (float)PyFloat_AsDouble(a) };
      call->result_kind = BP_F32; call->result = x.u; return true;
    }
#endif
#ifdef CID(from_f32)
    case CID(from_f32): { union { u32 u; float f; } x = { .u = (u32)f[0] }; return bp_add(call, PyFloat_FromDouble(x.f)); }
#endif
#ifdef CID(to_bool)
    case CID(to_bool):
      a = bp_get(call, f[0]); if (!a) return false;
      if (!PyBool_Check(a)) { PyErr_SetString(PyExc_TypeError, "expected a bool"); return false; }
      call->result_kind = BP_BOOL; call->result = a == Py_True; return true;
#endif
#ifdef CID(from_bool)
    case CID(from_bool): return bp_add(call, PyBool_FromLong(f[0] != 0));
#endif
#ifdef CID(to_string)
    case CID(to_string): {
      a = bp_get(call, f[0]); if (!a) return false;
      if (!PyUnicode_Check(a)) { PyErr_SetString(PyExc_TypeError, "expected a string"); return false; }
      Py_ssize_t n = PyUnicode_GetLength(a);
      call->buffer = malloc((n ? n : 1) * sizeof(u32));
      if (!call->buffer) { PyErr_NoMemory(); return false; }
      for (Py_ssize_t i = 0; i < n; ++i) call->buffer[i] = PyUnicode_ReadChar(a, i);
      call->length = n; call->result_kind = BP_STRING; return true;
    }
#endif
#ifdef CID(from_string)
    case CID(from_string): return bp_add(call, bp_text(call));
#endif
#ifdef CID(get_item)
    case CID(get_item):
      a = bp_get(call, f[0]); b = bp_get(call, f[1]); if (!a || !b) return false;
      return bp_add(call, PyObject_GetItem(a, b));
#endif
#ifdef CID(set_item)
    case CID(set_item):
      a = bp_get(call, f[0]); b = bp_get(call, f[1]); c = bp_get(call, f[2]);
      return a && b && c && PyObject_SetItem(a, b, c) == 0;
#endif
#ifdef CID(getattr)
    case CID(getattr):
      a = bp_get(call, f[0]); b = bp_text(call); if (!a || !b) { Py_XDECREF(b); return false; }
      result = PyObject_GetAttr(a, b); Py_DECREF(b); return bp_add(call, result);
#endif
#ifdef CID(call)
    case CID(call):
      a = bp_get(call, f[0]); b = bp_get(call, f[1]); c = bp_get(call, f[2]);
      if (!a || !b || !c) return false;
      if (!PyTuple_Check(b) || !PyDict_Check(c)) {
        PyErr_SetString(PyExc_TypeError, "call requires a tuple of arguments and a dict of keywords"); return false;
      }
      return bp_add(call, PyObject_Call(a, b, c));
#endif
#ifdef CID(builtins)
    case CID(builtins):
      a = PyImport_ImportModule("builtins"); b = bp_text(call);
      if (!a || !b) { Py_XDECREF(a); Py_XDECREF(b); return false; }
      result = PyObject_GetAttr(a, b); Py_DECREF(a); Py_DECREF(b); return bp_add(call, result);
#endif
#ifdef CID(none)
    case CID(none): return bp_add(call, Py_NewRef(Py_None));
#endif
#ifdef CID(empty_dict)
    case CID(empty_dict): return bp_add(call, PyDict_New());
#endif
#ifdef CID(tuple)
    case CID(tuple):
#endif
#ifdef CID(list)
    case CID(list):
#endif
      result = PyTuple_New(call->length);
      if (!result) return false;
      for (size_t i = 0; i < call->length; ++i) {
        a = bp_get(call, call->buffer[i]);
        if (!a) { Py_DECREF(result); return false; }
        PyTuple_SET_ITEM(result, i, Py_NewRef(a));
      }
#ifdef CID(list)
      if (call->effect == CID(list)) { a = PySequence_List(result); Py_DECREF(result); result = a; }
#endif
      return bp_add(call, result);
#ifdef CID(truthy)
    case CID(truthy): {
      a = bp_get(call, f[0]); if (!a) return false;
      int truth = PyObject_IsTrue(a); if (truth < 0) return false;
      call->result_kind = BP_BOOL; call->result = truth; return true;
    }
#endif
#ifdef CID(to_bytes)
    case CID(to_bytes): {
      a = bp_get(call, f[0]); if (!a) return false;
      // Any C-contiguous buffer: bytes, bytearray, memoryview, array.array, ...
      Py_buffer view;
      if (PyObject_GetBuffer(a, &view, PyBUF_C_CONTIGUOUS) < 0) return false;
      call->buffer = malloc((view.len ? view.len : 1) * sizeof(u32));
      if (!call->buffer) { PyBuffer_Release(&view); PyErr_NoMemory(); return false; }
      const unsigned char* bytes = view.buf;
#ifdef Py_GIL_DISABLED
      // Buffer exports pin storage, but do not stop bytearray slice writes.
      // Other exporters/views require caller synchronization against alias writes.
      Py_BEGIN_CRITICAL_SECTION(a);
#endif
      for (Py_ssize_t i = 0; i < view.len; ++i) call->buffer[i] = bytes[i];
#ifdef Py_GIL_DISABLED
      Py_END_CRITICAL_SECTION();
#endif
      call->length = view.len; call->result_kind = BP_BYTES;
      PyBuffer_Release(&view);
      return true;
    }
#endif
#ifdef CID(from_bytes)
    case CID(from_bytes): {
      result = PyBytes_FromStringAndSize(NULL, call->length);
      if (!result) return false;
      char* out = PyBytes_AS_STRING(result);
      for (size_t i = 0; i < call->length; ++i) {
        if (call->buffer[i] > 255) {
          Py_DECREF(result);
          PyErr_Format(PyExc_ValueError, "byte %zu is %u, outside range(256)", i, call->buffer[i]);
          return false;
        }
        out[i] = (char)call->buffer[i];
      }
      return bp_add(call, result);
    }
#endif
#ifdef CID(import_module)
    case CID(import_module):
      a = bp_text(call); if (!a) return false;
      result = PyImport_Import(a); Py_DECREF(a); return bp_add(call, result);
#endif
#ifdef CID(len)
    case CID(len): {
      a = bp_get(call, f[0]); if (!a) return false;
      Py_ssize_t n = PyObject_Length(a); if (n < 0) return false;
      if ((u64)n > NAT_IMM) { PyErr_SetString(PyExc_OverflowError, "length exceeds Bend Nat range"); return false; }
      call->result_kind = BP_NAT; call->result = n; return true;
    }
#endif
#ifdef CID(type_error)
    case CID(type_error):
      a = bp_text(call); if (!a) return false;
      PyErr_SetObject(PyExc_TypeError, a); Py_DECREF(a); return false;
#endif
    default: PyErr_SetString(PyExc_RuntimeError, "unsupported Python effect"); return false;
  }
}

static PyObject* bp_run(BpCall* call, BpOperation start) {
  call->key = bp_call_key();
  call->runtime = bp_runtime_acquire();
  bool ok = call->runtime != NULL && bp_native(call, start);
  while (ok && !call->done) {
    ok = bp_native(call, BP_RESUME);
    if (!ok || call->done) break;
    if (call->native_more) {
      ok = PyErr_CheckSignals() == 0;
      continue;
    }
    ok = bp_effect(call);
    // Conversions returning native data retain the buffer until BP_RESUME packs it.
    if (call->result_kind != BP_STRING && call->result_kind != BP_BYTES) { free(call->buffer); call->buffer = NULL; }
    call->pending = ok;
  }
  PyObject* result = NULL;
  if (ok) {
    PyObject* object = call->module ? call->module : bp_get(call, call->result);
    if (object) result = Py_NewRef(object);
  } else {
    if (!PyErr_Occurred()) PyErr_SetString(call->view_exception ? call->view_exception : PyExc_RuntimeError,
      call->view_error ? call->view_error : call->error);
    // A Python exception only aborts its invocation. Native failures discard
    // the damaged instance rather than affecting other callers.
    if (call->runtime && !call->error[0]) bp_native(call, BP_DROP);
  }
  // Return the lease before decref: finalizers can invoke this extension too.
  if (call->runtime) bp_runtime_release(call->runtime);
  call->runtime = NULL;
  free(call->buffer);
  for (size_t i = 0; i < call->view_count; ++i)
    if (call->views[i].live) PyBuffer_Release(&call->views[i].buffer);
  PyMem_Free(call->views);
  for (size_t i = 0; i < call->count; ++i) Py_DECREF(call->objects[i]);
  PyMem_Free(call->objects);
  return result;
}

static PyObject* bp_call_python(PyObject* self, PyObject* args, PyObject* kwargs) {
  bp_assert_attached();
  // Older CPython can reuse a single-phase module in another interpreter
  // without invoking PyInit again; enforce the boundary on every call too.
  if (PyInterpreterState_GetID(PyInterpreterState_Get()) != 0) {
    PyErr_SetString(PyExc_RuntimeError, "Bend calls require the main Python interpreter"); return NULL;
  }
  BpFunction* entry = (BpFunction*)self;
  BpCall call = { .function = entry->function, .release_gil = entry->release_gil,
    .nargs = (size_t)PyTuple_Size(args) };
#ifdef BENDPY_HAS_BLAS
  call.blas = entry->blas;
#endif
  for (size_t i = 0; i < call.nargs; ++i) {
    if (!bp_add(&call, Py_NewRef(PyTuple_GetItem(args, i)))) goto fail;
  }
  // C callers (PyObject_Call) may pass their own dict; like a Python **kwargs
  // function, Bend gets a fresh one it may mutate or return.
  if (!bp_add(&call, kwargs ? PyDict_Copy(kwargs) : PyDict_New())) goto fail;
  return bp_run(&call, BP_START);
fail:
  for (size_t i = 0; i < call.count; ++i) Py_DECREF(call.objects[i]);
  PyMem_Free(call.objects);
  return NULL;
}

static struct PyModuleDef bp_definition = {
  PyModuleDef_HEAD_INIT, BENDPY_MODULE_NAME,
  "Native Python bindings written in Bend.", -1, NULL
};

PyMODINIT_FUNC BENDPY_INIT(void) {
  bp_assert_attached();
  if (PyInterpreterState_GetID(PyInterpreterState_Get()) != 0) {
    PyErr_SetString(PyExc_ImportError, "Bend currently supports only the main Python interpreter"); return NULL;
  }
  if (PyType_Ready(&bp_function_type) < 0) return NULL;
#ifdef BENDPY_HAS_BLAS
  if (PyType_Ready(&bp_blas_type) < 0) return NULL;
#endif
  PyObject* module = PyModule_Create(&bp_definition);
  if (!module) return NULL;
#ifdef Py_GIL_DISABLED
  PyUnstable_Module_SetGIL(module, Py_MOD_GIL_NOT_USED);
#endif
  BpCall call = { .module = module };
#ifdef BENDPY_HAS_BLAS
  call.blas = (BpBlasCache*)PyType_GenericAlloc(&bp_blas_type, 0);
  if (!call.blas) { Py_DECREF(module); return NULL; }
#endif
  PyObject* result = bp_run(&call, BP_INIT);
#ifdef BENDPY_HAS_BLAS
  Py_DECREF(call.blas);
#endif
  Py_DECREF(module);
  return result;
}
