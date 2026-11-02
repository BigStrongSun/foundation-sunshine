#include "clipboard_x11.h"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <optional>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

#include "src/clipboard_blob_store.h"

extern char **environ;

namespace platf::clipboard_x11 {
  struct watcher_t::impl_t {
    std::atomic<bool> running {false}, ready {false};
    std::mutex mu;
    std::condition_variable cv;
    std::optional<png_t> outgoing;
    std::thread worker;
    int fd = -1;
    pid_t pid = -1;
    sink_fn image;
    text_sink_fn text;

    bool queue(std::uint8_t kind, png_t bytes) {
      if (!ready || bytes.empty() || bytes.size() > clipboard_blob_store::kMaxBlobBytes) return false;
      png_t packet {kind};
      const auto n = static_cast<std::uint32_t>(bytes.size());
      for (int shift : {24, 16, 8, 0}) packet.push_back((n >> shift) & 255);
      packet.insert(packet.end(), bytes.begin(), bytes.end());
      std::lock_guard lock(mu);
      outgoing = std::move(packet);
      return true;
    }

    void loop() {
      png_t input, output;
      std::size_t sent = 0;
      auto progress = std::chrono::steady_clock::now();
      while (running) {
        if (output.empty()) {
          std::lock_guard lock(mu);
          if (outgoing) { output = std::move(*outgoing); outgoing.reset(); sent = 0; }
        }
        pollfd p {fd, static_cast<short>(POLLIN | (output.empty() ? 0 : POLLOUT)), 0};
        const int rc = poll(&p, 1, 50);
        if (rc < 0) { if (errno == EINTR) continue; break; }
        if (p.revents & (POLLERR | POLLNVAL)) break;
        if (p.revents & POLLOUT) {
          auto n = send(fd, output.data() + sent, output.size() - sent, MSG_NOSIGNAL | MSG_DONTWAIT);
          if (n > 0) { sent += n; progress = std::chrono::steady_clock::now(); if (sent == output.size()) output.clear(); }
          else if (errno != EAGAIN && errno != EINTR) break;
        }
        if (p.revents & POLLIN) {
          std::uint8_t chunk[65536];
          auto n = recv(fd, chunk, sizeof(chunk), MSG_DONTWAIT);
          if (n == 0) break;
          if (n < 0) { if (errno == EAGAIN || errno == EINTR) continue; break; }
          input.insert(input.end(), chunk, chunk + n);
          progress = std::chrono::steady_clock::now();
          bool invalid = false;
          while (input.size() >= 5) {
            std::uint32_t size = 0;
            for (int i = 1; i <= 4; ++i) size = (size << 8) | input[i];
            if (size > clipboard_blob_store::kMaxBlobBytes) { invalid = true; break; }
            if (input.size() < size + 5ULL) break;
            auto kind = input[0];
            png_t bytes(input.begin() + 5, input.begin() + 5 + size);
            input.erase(input.begin(), input.begin() + 5 + size);
            if (kind == 'R' && size == 0) { ready = true; cv.notify_all(); }
            else if (kind == 'T' && text) text(std::string(bytes.begin(), bytes.end()));
            else if (kind == 'P' && image) image(std::move(bytes));
            else { invalid = true; break; }
          }
          if (invalid) break;
        }
        if (p.revents & POLLHUP) break;
        if ((!input.empty() || !output.empty()) && std::chrono::steady_clock::now() - progress > std::chrono::seconds(10)) break;
      }
      ready = false;
      running = false;
      cv.notify_all();
    }
  };

  watcher_t::watcher_t(): impl(std::make_unique<impl_t>()) {}
  watcher_t::~watcher_t() { stop(); }
  bool watcher_t::alive() const { return impl->ready; }

  bool watcher_t::start(sink_fn image, text_sink_fn text) {
    if (!std::getenv("DISPLAY") || std::getenv("WAYLAND_DISPLAY")) return false;
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair)) return false;
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pair[1], STDIN_FILENO);
    posix_spawn_file_actions_adddup2(&actions, pair[1], STDOUT_FILENO);
    posix_spawn_file_actions_addclosefrom_np(&actions, 3);
    char python[] = "/usr/bin/python3";
    char script[] = SUNSHINE_ASSETS_DIR "/clipboard-x11-helper.py";
    char *args[] = {python, script, nullptr};
    const int rc = posix_spawn(&impl->pid, python, &actions, nullptr, args, environ);
    posix_spawn_file_actions_destroy(&actions);
    close(pair[1]);
    if (rc) { close(pair[0]); impl->pid = -1; return false; }
    impl->fd = pair[0]; impl->image = std::move(image); impl->text = std::move(text);
    impl->running = true;
    impl->worker = std::thread([this] { impl->loop(); });
    std::unique_lock lock(impl->mu);
    impl->cv.wait_for(lock, std::chrono::seconds(4), [this] {return impl->ready || !impl->running;});
    const bool ok = impl->ready;
    lock.unlock();
    if (!ok) stop();
    return ok;
  }

  void watcher_t::stop() {
    impl->running = false;
    if (impl->fd >= 0) shutdown(impl->fd, SHUT_RDWR);
    if (impl->worker.joinable()) impl->worker.join();
    if (impl->fd >= 0) {close(impl->fd); impl->fd = -1;}
    if (impl->pid > 0) {
      for (int i = 0; i < 20; ++i) {
        if (waitpid(impl->pid, nullptr, WNOHANG) != 0) {impl->pid = -1; break;}
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
      }
      if (impl->pid > 0) {kill(impl->pid, SIGKILL); waitpid(impl->pid, nullptr, 0); impl->pid = -1;}
    }
    impl->ready = false;
  }
  bool watcher_t::set_image(png_t bytes) {return impl->queue('P', std::move(bytes));}
  bool watcher_t::set_text(std::string text) {return impl->queue('T', png_t(text.begin(), text.end()));}
}
