#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace platf::clipboard_x11 {
  // GTK runs in a separate process: the tray and capture threads must never
  // enter a second GTK main loop or wait on a foreign selection owner.
  class watcher_t {
  public:
    using png_t = std::vector<std::uint8_t>;
    using sink_fn = std::function<void(png_t)>;
    using text_sink_fn = std::function<void(std::string)>;
    watcher_t();
    ~watcher_t();
    bool start(sink_fn image, text_sink_fn text);
    void stop();
    bool alive() const;
    bool set_image(png_t bytes);
    bool set_text(std::string text);
  private:
    struct impl_t;
    std::unique_ptr<impl_t> impl;
  };
}
