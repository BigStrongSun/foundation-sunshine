// Standalone adapter for exercising the real C++ watcher with the GTK/Xvfb
// integration tests. Uses the helper's private framing on stdin/stdout.
#include "src/platform/linux/clipboard_x11.h"
#include "src/clipboard_blob_store.h"
#include <cstdint>
#include <iostream>
#include <mutex>

int main() {
  std::mutex output;
  auto emit = [&](char kind, const std::uint8_t *data, std::size_t size) {
    std::lock_guard lock(output);
    std::cout.put(kind);
    for (int shift : {24, 16, 8, 0}) std::cout.put((size >> shift) & 255);
    std::cout.write(reinterpret_cast<const char *>(data), size);
    std::cout.flush();
  };
  platf::clipboard_x11::watcher_t watcher;
  if (!watcher.start([&](auto png) {emit('P', png.data(), png.size());},
      [&](auto text) {emit('T', reinterpret_cast<const std::uint8_t *>(text.data()), text.size());})) return 1;
  emit('R', nullptr, 0);
  char header[5];
  while (std::cin.read(header, 5)) {
    std::uint32_t size = 0;
    for (int i=1; i<=4; ++i) size = (size << 8) | static_cast<unsigned char>(header[i]);
    if (size > clipboard_blob_store::kMaxBlobBytes) break;
    std::vector<std::uint8_t> bytes(size);
    if (!std::cin.read(reinterpret_cast<char *>(bytes.data()), size)) break;
    if (header[0]=='T') watcher.set_text(std::string(bytes.begin(), bytes.end()));
    else if (header[0]=='P') watcher.set_image(std::move(bytes));
    else break;
  }
  watcher.stop();
  return watcher.alive() ? 2 : 0;
}
