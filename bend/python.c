// CPython and private Bend 2.0.28 runtime boundary. All runtime access is locked.
#include <Python.h>
#include <setjmp.h>

#if BANGS
#error "GPU entry points are not supported by this embedding."
#endif

typedef struct {
  PyMethodDef method;
  Term function;
  bool release_gil;
  char* name;
} BendExport;

typedef enum { BP_UNIT, BP_OBJECT, BP_U32, BP_F32, BP_BOOL, BP_STRING, BP_NAT } BpValue;
typedef enum { BP_INIT, BP_START, BP_RESUME, BP_DROP } BpOperation;

typedef struct {
  PyObject** objects;
  size_t count, capacity, nargs;
  PyObject* module;
  Term continuation, argument, function;
  Term fields[5];
  u32 effect;
  u32* items;
  size_t length;
  u32* text;
  size_t text_length;
  BpValue result_kind;
  u64 result;
  bool release_gil, pending, done;
  char error[256];
} BpCall;

static pthread_mutex_t bp_mutex = PTHREAD_MUTEX_INITIALIZER;
static jmp_buf bp_escape;
static bool bp_poisoned;
static char bp_error[256];

static void bp_assert_attached(void) {
  // PyThreadState_Get fails fatally if this thread is detached, including on
  // free-threaded CPython. Traditional builds must also own the GIL.
  (void)PyThreadState_Get();
#ifndef Py_GIL_DISABLED
  if (!PyGILState_Check()) Py_FatalError("Bend bridge entered Python without the GIL");
#endif
}

static void bendpy_panic(const char* message) {
  bp_poisoned = true;
  snprintf(bp_error, sizeof(bp_error), "%s", message);
  longjmp(bp_escape, 1);
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

static u32 bp_unbox(Env e, Term object) {
  if (term_tag(object) == TAG_PAK) return (u32)term_loc(object);
  Term field;
  spare_free(e, 0, ctr_take(e, object, 1, &field));
  return (u32)field;
}

static void bp_read_list(Env e, Term list, BpCall* call, bool string) {
  size_t length = 0, capacity = 16;
  u32* data = bp_alloc(capacity * sizeof(u32));
  // Store before traversal so a runtime failure still frees the host buffer.
  if (string) call->text = data; else call->items = data;
  u32 cons = string ? CID(SCon) : CID(Con);
  while (term_aux(list) == cons) {
    Term fields[2];
    spare_free(e, 1, ctr_take(e, list, 2, fields));
    if (length == capacity) {
      capacity *= 2;
      u32* grown = realloc(data, capacity * sizeof(u32));
      if (grown == NULL) bendpy_panic("unable to grow Bend bridge buffer");
      data = grown;
      if (string) call->text = data; else call->items = data;
    }
    data[length++] = string ? (u32)fields[0] : bp_unbox(e, fields[0]);
    list = fields[1];
  }
  if (string) call->text_length = length; else call->length = length;
}

static Term bp_pack(Env e, BpCall* call) {
  switch (call->result_kind) {
    case BP_UNIT: return term_pak(CID(Unit), 0);
    case BP_OBJECT: return term_pak(CID(PyObject), call->result);
    case BP_BOOL: return term_pak(call->result ? CID(True) : CID(False), 0);
    case BP_STRING: {
      Term text = term_pak(CID(SNil), 0);
      for (size_t i = call->text_length; i > 0; --i)
        text = io_node(e, CID(SCon), call->text[i - 1], text);
      return text;
    }
    default: return (Term)call->result;
  }
}

// Decode Bend-owned arguments while holding the runtime lock. The resulting
// handles and codepoints are plain C data; Python callbacks run after unlock.
static void bp_decode(Env e, BpCall* call) {
  Term* f = call->fields;
  switch (call->effect) {
#ifdef CID(export)
    case CID(export):
      bp_read_list(e, f[0], call, true);
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
      f[0] = bp_unbox(e, f[0]); bp_read_list(e, f[1], call, true); break;
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
      bp_read_list(e, f[0], call, true); break;
#ifdef CID(tuple)
    case CID(tuple):
#endif
#ifdef CID(list)
    case CID(list):
#endif
      bp_read_list(e, f[0], call, false); break;
#ifdef CID(from_bool)
    case CID(from_bool): f[0] = term_aux(f[0]) == CID(True); break;
#endif
#ifdef CID(from_u32)
    case CID(from_u32): break;
#endif
#ifdef CID(from_f32)
    case CID(from_f32): break;
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
  // Detach while waiting even when the export retains the GIL for computation.
  PyThreadState* thread = PyEval_SaveThread();
  pthread_mutex_lock(&bp_mutex);
  if (!call->release_gil) { PyEval_RestoreThread(thread); thread = NULL; }
  if (bp_poisoned) {
    snprintf(call->error, sizeof(call->error), "Bend runtime is unusable after a previous failure");
  } else if (setjmp(bp_escape)) {
    snprintf(call->error, sizeof(call->error), "%s", bp_error);
  } else {
    if (operation == BP_INIT) {
      if (CORPUS != NULL) bendpy_panic("Bend runtime has already been initialized");
      corpus_setup(false, 1, 0);
      u64 bytes = 1ull << 31;
      io_stk = pool_mmap(bytes + 16384);
      if (mprotect((char*)io_stk + bytes, 16384, PROT_NONE))
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
          args = io_node(e, CID(Con), term_pak(CID(PyObject), i - 1), args);
        Term input = io_node(e, CID(PyCall), args, term_pak(CID(PyObject), call->nargs));
        call->continuation = bp_apply(e, call->function, input);
        call->argument = term_clo(FID_IO_EMIT, 0);
        break;
      }
      case BP_RESUME: {
        if (call->pending) {
          call->argument = bp_pack(e, call);
          call->pending = false;
          free(call->text); call->text = NULL;
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
  pthread_mutex_unlock(&bp_mutex);
  if (thread) PyEval_RestoreThread(thread);
  bp_assert_attached();
  return call->error[0] == 0;
}

// Each invocation owns a reference arena. Handles never contain PyObject*
// addresses. Check them before access, and release the arena after native locks.
static PyObject* bp_get(BpCall* call, u64 index) {
  bp_assert_attached();
  if (index >= call->count) {
    PyErr_SetString(PyExc_ValueError, "invalid Python object handle");
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
  for (size_t i = 0; i < call->text_length; ++i) {
    if (call->text[i] > 0x10ffff) {
      PyErr_SetString(PyExc_ValueError, "Bend String contains an invalid Unicode codepoint");
      return NULL;
    }
  }
  return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, call->text, call->text_length);
}

static PyObject* bp_call_python(PyObject*, PyObject*, PyObject*);

static void bp_export_free(PyObject* capsule) {
  bp_assert_attached();
  BendExport* entry = PyCapsule_GetPointer(capsule, "bend.export");
  free(entry->name); free(entry);
}

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
  BendExport* entry = calloc(1, sizeof(BendExport));
  if (entry) entry->name = strdup(text);
  Py_DECREF(name);
  if (!entry || !entry->name) { free(entry); PyErr_NoMemory(); return false; }
  entry->function = call->fields[1]; entry->release_gil = call->fields[2];
  entry->method = (PyMethodDef){ entry->name, (PyCFunction)(void(*)(void))bp_call_python,
    METH_VARARGS | METH_KEYWORDS, "A native Bend function." };
  PyObject* capsule = PyCapsule_New(entry, "bend.export", bp_export_free);
  if (!capsule) { free(entry->name); free(entry); return false; }
  PyObject* function = PyCFunction_NewEx(&entry->method, capsule, NULL);
  Py_DECREF(capsule);
  if (!function) return false;
  int added = PyModule_AddObject(call->module, entry->name, function);
  if (added < 0) Py_DECREF(function);
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
#ifdef CID(to_f32)
    case CID(to_f32): {
      a = bp_get(call, f[0]); if (!a) return false;
      if (!PyFloat_Check(a)) { PyErr_SetString(PyExc_TypeError, "expected a float"); return false; }
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
      call->text = malloc((n ? n : 1) * sizeof(u32));
      if (!call->text) { PyErr_NoMemory(); return false; }
      for (Py_ssize_t i = 0; i < n; ++i) call->text[i] = PyUnicode_ReadChar(a, i);
      call->text_length = n; call->result_kind = BP_STRING; return true;
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
        a = bp_get(call, call->items[i]);
        if (!a) { Py_DECREF(result); return false; }
        PyTuple_SET_ITEM(result, i, Py_NewRef(a));
      }
#ifdef CID(list)
      if (call->effect == CID(list)) { a = PySequence_List(result); Py_DECREF(result); result = a; }
#endif
      return bp_add(call, result);
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
  bool ok = bp_native(call, start);
  while (ok && !call->done) {
    ok = bp_native(call, BP_RESUME);
    if (!ok || call->done) break;
    ok = bp_effect(call);
    free(call->items); call->items = NULL;
    // to_string owns the return buffer until BP_RESUME packs it.
    if (call->result_kind != BP_STRING) { free(call->text); call->text = NULL; }
    call->pending = ok;
  }
  PyObject* result = NULL;
  if (ok) {
    PyObject* object = call->module ? call->module : bp_get(call, call->result);
    if (object) result = Py_NewRef(object);
  } else {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_RuntimeError, call->error);
    // A Python exception aborts this invocation, not the shared runtime.
    if (!call->error[0]) bp_native(call, BP_DROP);
  }
  free(call->text); free(call->items);
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
  BendExport* entry = PyCapsule_GetPointer(self, "bend.export");
  if (!entry) return NULL;
  BpCall call = { .function = entry->function, .release_gil = entry->release_gil,
    .nargs = (size_t)PyTuple_Size(args) };
  for (size_t i = 0; i < call.nargs; ++i) {
    if (!bp_add(&call, Py_NewRef(PyTuple_GetItem(args, i)))) goto fail;
  }
  if (!bp_add(&call, kwargs ? Py_NewRef(kwargs) : PyDict_New())) goto fail;
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
