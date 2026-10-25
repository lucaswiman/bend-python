// Exact runtime excerpts from Bend 2.0.38 generated C.
// Text-patch fixture only; omitted surrounding runtime is not C-compilable.
// Source SHA256: ccf8d16ac124c06eb64dbd7cb7cee994b482f3ce0b7f866821a7c56ba2efb50a

#define _GNU_SOURCE

#if __has_attribute(preserve_none) && __has_attribute(preserve_most) \
  && __OPTIMIZE__
#define PRESERVE(A) __attribute__((A))
#else
#define PRESERVE(A)
#endif

#define WL_OPEN    { WL_BANK u32 rn;

#define WL_BANK Term r0, r1, r2, r3;

static u64*    CORPUS;
static u64    ALC[CUBE_T + 1][3 * NCLS_ALL] __attribute__((aligned(128)));
static u32    KEEP_WORDS;
static u32    CUBE_LOG = 7;
static u32    bank_lock;

static u32             pool_size;

static bool io_gpu;
static DEV Term*  io_stk;

OUTLINE void err_fail(const char* msg) {
  if (err_claim()) fflush(stdout);
  fprintf(stderr, "bend: %s\n", msg);
  _exit(1);
}

OUTLINE void pool_open(void) {
  static bool up;
  if (up) {
    return;
  }
  up = true;
  for (u32 w = 1; w < pool_size; w += 1) {
    pthread_t tid;
    if (pthread_create(&tid, NULL, pool_work, (void*)(uintptr_t)w)) {
      err_fail("pthread_create");
    }
  }
}

static u64 corpus_size;

static void corpus_lay(u64* H, u64 size) {
  u64 span = size / 8;
  u64 cap  = span > HEAP_OFF ? (span - HEAP_OFF) / (PAGE_LEN + 10) : 0;
  if (cap <= CUBE) {
    err_fail("the GPU span is under the rings, stacks and a page per lane");
  }
  cap = cap < ~0u ? cap : ~0u - 1;
  u64 at = HEAP_OFF + (cap << PAGE_BITS);
  for (u32 c = 0; c < NCLS_ALL; c += 1) {
    Bank* b = bank_at(H, c);
    memcpy(H + at, H + b->off, b->wr * sizeof(u64));
    b->off  = at;
    at     += 2 * (cap >> ((c < NCLS ? NCLS : c) - PAGE_BITS));
  }
  corpus_size = size;
  a32_store_rel(a32_at(H, H_CAP), (u32)cap);
}

int main(int argc, char** argv) {
}

static u64* corpus_setup(bool gpu, long threads, u64 bytes) {
  io_gpu     = gpu;
  KEEP_WORDS = gpu ? CHUNK : CAP_WORDS;
  u64 size   = (!gpu ? 1ull << 33 : bytes != 0 ? bytes : gpu_span())
    & ~16383ull;
  CORPUS     = gpu ? gpu_map(size) : corpus_map(size);
  u64* H     = CORPUS;
#if BEND_CUDA
  if (gpu) {
    cuMemsetD8((CUdeviceptr)(uintptr_t)H, 0, STAK_OFF * 8);
    cuCtxSynchronize();
  }
#endif
  corpus_lay(H, size);
  memcpy(H + STAT_OFF, STAT_IMG, STAT_LEN * sizeof(u64));
  a32_store(a32_at(H, H_BUMP), 1);
  if (gpu) {
    gpu_load(size);
  }
  pool_size = threads < 1 ? 1 : threads < CUBE_T ? threads : CUBE_T;
  return H;
}

INLINE void rfc_bump(Env e, u64 r, u32 k) {
  u32 c = a32_add(a32_at(e.mem, r), k);
  if ((c & RFC_CNT) >= RFC_CNT - k) {
    err_post(e.mem, ERR_CNTS);
  }
}

FAR void term_drop(Env e, Term t) {
  DEV u64* H = e.mem;
  u64  cur = 0;
  Term c0  = 0;
  u32  step = 0;
  for (;;) {
    if (!term_triv(t) && term_rfc(t)) {
      u64      r = term_loc(t);
      DEV u32* p = a32_at(H, r);
      if ((a32_sub_rel(p, 1) & RFC_CNT) != 1) {
        t = 0;
      } else {
        a32_acq(p);
        t = (t & ~(RFC_BIT | LOC_MASK)) | (H[r] >> 24);
        heap_free(e, 0, r);
      }
    }
    // Remaining traversal omitted: this is a text-patch fixture.
