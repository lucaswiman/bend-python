/* Test-only entry into the actual generated allocator; no production ABI. */
__attribute__((visibility("default")))
int bendsym_allocator_probe(u32 cls, u64 *out) {
  if (bp_current || cls > 16) return 900;
  BpRuntime *runtime = NULL;
  if (posix_memalign((void **)&runtime, _Alignof(BpRuntime), sizeof(*runtime))) return 901;
  memset(runtime, 0, sizeof(*runtime));
  runtime->cube_log = 7;
  bp_current = runtime;
  if (setjmp(runtime->escape)) {
    bp_current = NULL;
    if (runtime->heap) munmap(runtime->heap, runtime->heap_size);
    free(runtime);
    return 902;
  }
  corpus_setup(false, 1, 0);
  Env e = {CORPUS, ALC[0]};
  u32 n = CAP_WORDS >> cls ? CAP_WORDS >> cls : 1;
  u64 *locations = malloc(2ull * n * sizeof(u64));
  int result = 0;
  if (!locations) { result = 903; goto done; }
  #define REQUIRE(p, code) do { if (!(p)) { result = code; goto done; } } while (0)
  REQUIRE(KEEP_WORDS == CAP_WORDS && !io_gpu && pool_size == 1, 100);
  REQUIRE(ALC_AT(e, cls) == 0 && ALC_LEN(e, cls) == 0 && ALC_COLD(e, cls) == 0, 101);
  REQUIRE(bank_at(e.mem, cls)->rd == 0, 102);
  for (u32 i = 0; i < 2*n; ++i) {
    u64 loc = heap_alloc(e, cls);
    REQUIRE(loc >= HEAP_OFF && (!i || loc > locations[i-1]), 103);
    locations[i] = loc;
    e.mem[loc] = 0x123400000000ull + i;
    if (cls) e.mem[loc+1] = 0x987600000000ull + i;
  }
  REQUIRE(ALC_AT(e, cls) == 0 && ALC_LEN(e, cls) == 0, 104);
  for (u32 i = 0; i+1 < n; ++i) heap_free(e, cls, locations[i]);
  out[0] = CAP_WORDS;
  out[1] = ALC_LEN(e, cls);
  REQUIRE(ALC_LEN(e, cls) == ((u64)(n-1) << cls) && ALC_COLD(e, cls) == 0, 105);
  heap_free(e, cls, locations[n-1]);
  REQUIRE(ALC_AT(e, cls) == 0 && ALC_LEN(e, cls) == 0, 106);
  REQUIRE(ALC_COLD(e, cls) == locations[n-1] && bank_at(e.mem, cls)->rd == 0, 107);
  out[2] = ALC_LEN(e, cls);
  out[3] = ALC_COLD(e, cls) != 0;
  out[4] = bank_at(e.mem, cls)->rd;
  for (u32 i = n; i < 2*n; ++i) heap_free(e, cls, locations[i]);
  REQUIRE(ALC_AT(e, cls) == 0 && ALC_LEN(e, cls) == 0, 108);
  REQUIRE(ALC_COLD(e, cls) == locations[2*n-1] && bank_at(e.mem, cls)->rd == 1, 109);
  out[5] = bank_at(e.mem, cls)->rd;
  u64 checksum = 0;
  for (u32 i = 0; i < 2*n; ++i) {
    u64 loc = heap_alloc(e, cls);
    REQUIRE(loc == locations[2*n-i-1], 110);
    if (cls) {
      #ifdef BEND_POISON
      REQUIRE(e.mem[loc+1] == (RFC_BIT | TAG_CTR << 56 | LOC_MASK), 111);
      #else
      REQUIRE(e.mem[loc+1] == 0x987600000000ull + (2*n-i-1), 112);
      #endif
      e.mem[loc+1] = 0x654300000000ull + i;
    }
    checksum += loc - locations[0];
    e.mem[loc] = 0xabcdef000000ull + i;
  }
  REQUIRE(ALC_AT(e, cls) == 0 && ALC_LEN(e, cls) == 0 && ALC_COLD(e, cls) == 0, 113);
  REQUIRE(bank_at(e.mem, cls)->rd == 0, 114);
  out[6] = 2*n;
  out[7] = checksum;
  out[8] = ((u64)n << cls);
  /* On this CPU compilation err_peek is constant false. Its device-only
     branch cannot be exercised by writing a fake error into host metadata. */
done:
  #undef REQUIRE
  free(locations);
  bp_current = NULL;
  munmap(runtime->heap, runtime->heap_size);
  free(runtime);
  return result;
}
