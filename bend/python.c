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

// An exported Bend function. Its fields are immutable after export, and its
// captureless Term holds no heap reference, so any runtime instance may run it.
typedef struct {
  PyObject_HEAD
  Term function;
  bool release_gil;
  PyObject* name;
  PyObject* module;
} BpFunction;

typedef enum { BP_UNIT, BP_OBJECT, BP_U32, BP_F32, BP_BOOL, BP_STRING, BP_NAT, BP_BYTES } BpValue;
// A decoded Bend list: object handles, String codepoints, or raw U32 words.
typedef enum { BP_READ_OBJECTS, BP_READ_TEXT, BP_READ_WORDS } BpRead;
typedef enum { BP_INIT, BP_START, BP_RESUME, BP_DROP } BpOperation;

typedef struct {
  BpRuntime* runtime;
  PyObject** objects;
  size_t count, capacity, nargs;
  PyObject* module;
  Term continuation, argument, function;
  Term fields[5];
  u32 effect;
  u32* buffer;
  size_t length;
  BpValue result_kind;
  u64 result;
  u64 key;
  bool release_gil, pending, done;
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
static u32 bp_unbox(Env e, Term object) {
  if (term_aux(object) != CID(PyObject)) bendpy_panic("expected a Python object handle");
  if (term_tag(object) == TAG_PAK) return (u32)term_loc(object);
  if (term_tag(object) != TAG_CTR) bendpy_panic("unexpected Python object representation");
  Term field;
  spare_free(e, 0, ctr_take(e, object, 1, &field));
  return (u32)field;
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
    default: bendpy_panic("unsupported foreign effect in Python extension");
  }
}

static bool bp_native(BpCall* call, BpOperation operation) {
  bp_assert_attached();
#ifdef Py_GIL_DISABLED
  // Without a GIL, staying attached only delays stop-the-world pauses.
  PyThreadState* thread = PyEval_SaveThread();
#else
  PyThreadState* thread = call->release_gil ? PyEval_SaveThread() : NULL;
#endif
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
        break;
      }
      case BP_DROP:
        if (call->continuation) term_drop(e, call->continuation);
        break;
    }
  }
  bp_current = NULL;
  if (thread) PyEval_RestoreThread(thread);
  bp_assert_attached();
  return call->error[0] == 0;
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

static void bp_function_dealloc(PyObject* self) {
  BpFunction* function = (BpFunction*)self;
  Py_XDECREF(function->name); Py_XDECREF(function->module);
  PyObject_Free(self);
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
  .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
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
  BpFunction* function = module_name ? PyObject_New(BpFunction, &bp_function_type) : NULL;
  if (!function) { Py_DECREF(name); Py_XDECREF(module_name); return false; }
  function->function = call->fields[1]; function->release_gil = call->fields[2];
  function->name = name; function->module = module_name;
  int added = PyModule_AddObjectRef(call->module, text, (PyObject*)function);
  Py_DECREF(function);
  return added == 0;
}

static bool bp_effect(BpCall* call) {
  bp_assert_attached();
  Term* f = call->fields;
  PyObject *a = NULL, *b = NULL, *c = NULL, *result = NULL;
  call->result_kind = BP_UNIT;
  switch (call->effect) {
#ifdef CID(export)
    case CID(export): return bp_export(call);
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
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_RuntimeError, call->error);
    // A Python exception only aborts its invocation. Native failures discard
    // the damaged instance rather than affecting other callers.
    if (call->runtime && !call->error[0]) bp_native(call, BP_DROP);
  }
  // Return the lease before decref: finalizers can invoke this extension too.
  if (call->runtime) bp_runtime_release(call->runtime);
  call->runtime = NULL;
  free(call->buffer);
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
  PyObject* module = PyModule_Create(&bp_definition);
  if (!module) return NULL;
#ifdef Py_GIL_DISABLED
  PyUnstable_Module_SetGIL(module, Py_MOD_GIL_NOT_USED);
#endif
  BpCall call = { .module = module };
  PyObject* result = bp_run(&call, BP_INIT);
  Py_DECREF(module);
  return result;
}
