// Stable application ABI; audio/text stay in worker memory. No file or network transport.
#include "whisper.h"
#include <algorithm>
#include <memory>
#include <string>

#ifdef _WIN32
#define WL_API extern "C" __declspec(dllexport)
#else
#define WL_API extern "C" __attribute__((visibility("default")))
#endif

extern "C" int wl_context_uses_metal(whisper_context *);
struct Context {
    whisper_context * model = nullptr;
    int threads = 1;
    std::string text;
    ~Context() { if (model) whisper_free(model); }
};

static void silent_log(ggml_log_level, const char *, void *) {}
WL_API int wl_abi_version() { return 1; }
WL_API const char * wl_version() { return "whisper.cpp 1.9.4"; }

WL_API void * wl_create(const char * path, int gpu, int threads) {
    try {
        whisper_log_set(silent_log, nullptr); // also suppresses GGML logging
        auto result = std::make_unique<Context>();
        auto params = whisper_context_default_params();
        params.use_gpu = gpu != 0;
#if defined(__APPLE__) && defined(__x86_64__)
        params.flash_attn = gpu == 0; // older Intel Metal GPUs lack SIMD-group matrix kernels
#endif
        result->threads = std::max(1, threads);
        result->model = whisper_init_from_file_with_params(path, params);
        if (!result->model) return nullptr;
        return result.release();
    } catch (...) { return nullptr; }
}

WL_API int wl_uses_metal(void * ptr) {
    auto ctx = static_cast<Context *>(ptr);
    return ctx ? wl_context_uses_metal(ctx->model) : 0;
}
WL_API int wl_ftype(void * ptr) {
    auto ctx = static_cast<Context *>(ptr);
    return ctx ? whisper_model_ftype(ctx->model) : -1;
}
WL_API void wl_free(void * ptr) { delete static_cast<Context *>(ptr); }

WL_API int wl_transcribe(void * ptr, const float * pcm, int samples, const char * language) {
    auto ctx = static_cast<Context *>(ptr);
    if (!ctx || !pcm || samples < 1 || samples > 16000 * 181) return -1;
    ctx->text.clear();
    try {
        auto params = whisper_full_default_params(WHISPER_SAMPLING_BEAM_SEARCH);
        params.n_threads = ctx->threads;
        params.language = language && language[0] ? language : "auto";
        params.no_context = true;
        params.no_timestamps = true;
        params.print_special = false;
        params.print_progress = false;
        params.print_realtime = false;
        params.print_timestamps = false;
        params.token_timestamps = false;
        params.translate = false;
        params.temperature = 0.0f;
        params.temperature_inc = 0.0f;
        params.beam_search.beam_size = 5;
        params.suppress_blank = true;
        if (whisper_full(ctx->model, params, pcm, samples) != 0) return -2;
        for (int i = 0; i < whisper_full_n_segments(ctx->model); ++i) {
            if (whisper_full_get_segment_no_speech_prob(ctx->model, i) > params.no_speech_thold) continue;
            if (!ctx->text.empty()) ctx->text += " ";
            ctx->text += whisper_full_get_segment_text(ctx->model, i);
        }
        return 0;
    } catch (...) { ctx->text.clear(); return -3; }
}
WL_API const char * wl_text(void * ptr) {
    auto ctx = static_cast<Context *>(ptr);
    return ctx ? ctx->text.c_str() : "";
}
WL_API void wl_clear_text(void * ptr) {
    auto ctx = static_cast<Context *>(ptr);
    if (ctx) ctx->text.clear();
}
