// Compiled in the pinned whisper.cpp translation unit: its public API hides state backends.
// Check the initialized context, rather than a build flag or device enumeration.
extern "C" int wl_context_uses_metal(whisper_context * ctx) {
    if (!ctx || !ctx->state) return 0;
    for (auto backend : ctx->state->backends) {
        auto dev = ggml_backend_get_device(backend);
        auto reg = dev ? ggml_backend_dev_backend_reg(dev) : nullptr;
        if (reg && std::string(ggml_backend_reg_name(reg)) == "MTL") return 1;
    }
    return 0;
}
