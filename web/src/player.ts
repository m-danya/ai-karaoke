/** A single device audio clock drives both stems, the seekbar and the lyrics. */
export type PlaybackState = {
  position: number;
  duration: number;
  playing: boolean;
};
export class StemPlayer {
  context: AudioContext | null = null;
  buffers: AudioBuffer[] = [];
  sources: AudioBufferSourceNode[] = [];
  gains: GainNode[] = [];
  position = 0;
  started = 0;
  playing = false;
  duration = 0;
  volume = [1, 1];
  muted = [false, false];
  loop: [number, number] | null = null;
  abort: AbortController | null = null;
  generation = 0;
  envelope: number[] = [];
  onended: (() => void) | null = null;

  async unlock() {
    this.context ??= new AudioContext();
    if (this.context.state === "suspended") await this.context.resume();
  }
  async load(urls: string[], progress: (value: string) => void) {
    this.pause();
    this.abort?.abort();
    const token = ++this.generation;
    this.abort = new AbortController();
    this.position = this.duration = 0;
    this.loop = null;
    this.buffers = [];
    this.envelope = [];
    await this.unlock();
    progress("Загружаем дорожки на устройство…");
    const buffers = await Promise.all(
      urls.map(async (url) => {
        const response = await fetch(url, { signal: this.abort!.signal });
        if (!response.ok)
          throw new Error(`Не удалось загрузить дорожку (${response.status})`);
        return this.context!.decodeAudioData(await response.arrayBuffer());
      }),
    );
    if (token !== this.generation) return false;
    this.duration = Math.max(...buffers.map((b) => b.duration));
    // Equal lengths keep A–B looping in sync even if one encoded stem ends early.
    this.buffers = buffers.map((buffer) => {
      const length = Math.round(this.duration * buffer.sampleRate);
      if (buffer.length === length) return buffer;
      const padded = this.context!.createBuffer(
        buffer.numberOfChannels,
        length,
        buffer.sampleRate,
      );
      for (let channel = 0; channel < buffer.numberOfChannels; channel++)
        padded.copyToChannel(buffer.getChannelData(channel), channel);
      return padded;
    });
    const channel = buffers[0].getChannelData(0),
      hop = Math.max(1, Math.floor(buffers[0].sampleRate / 20));
    for (let i = 0; i < channel.length; i += hop) {
      let peak = 0;
      for (let j = i; j < Math.min(i + hop, channel.length); j += 8)
        peak = Math.max(peak, Math.abs(channel[j]));
      this.envelope.push(peak);
    }
    progress("");
    return true;
  }
  current(): number {
    if (!this.playing || !this.context) return this.position;
    const time =
      this.position + Math.max(0, this.context.currentTime - this.started);
    if (this.loop && time >= this.loop[1])
      return (
        this.loop[0] + ((time - this.loop[0]) % (this.loop[1] - this.loop[0]))
      );
    return Math.min(this.duration, time);
  }
  async play() {
    if (!this.buffers.length || this.playing) return;
    await this.unlock();
    if (this.position >= this.duration - 0.02) this.position = 0;
    const context = this.context!;
    const at = context.currentTime + 0.04;
    this.started = at;
    this.playing = true;
    this.sources = this.buffers.map((buffer, index) => {
      const source = context.createBufferSource();
      source.buffer = buffer;
      const gain = context.createGain();
      this.gains[index] = gain;
      gain.gain.value = this.muted[index] ? 0 : this.volume[index];
      source.connect(gain).connect(context.destination);
      if (this.loop) {
        source.loop = true;
        source.loopStart = this.loop[0];
        source.loopEnd = this.loop[1];
      }
      if (this.position < buffer.duration) source.start(at, this.position);
      return source;
    });
  }
  pause() {
    this.position = this.current();
    this.playing = false;
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {}
      source.disconnect();
    }
    this.sources = [];
    this.gains.forEach((g) => g.disconnect());
    this.gains = [];
  }
  async seek(time: number) {
    const resume = this.playing;
    this.pause();
    this.position = Math.max(0, Math.min(time, this.duration));
    if (
      this.loop &&
      (this.position < this.loop[0] || this.position >= this.loop[1])
    )
      this.loop = null;
    if (resume) await this.play();
  }
  async setLoop(loop: [number, number] | null) {
    const resume = this.playing;
    this.pause();
    this.loop = loop;
    if (loop && (this.position < loop[0] || this.position >= loop[1]))
      this.position = loop[0];
    if (resume) await this.play();
  }
  setGain(index: number, value: number, muted: boolean) {
    this.volume[index] = value;
    this.muted[index] = muted;
    if (this.gains[index])
      this.gains[index].gain.setTargetAtTime(
        muted ? 0 : value,
        this.context!.currentTime,
        0.015,
      );
  }
  tick(): PlaybackState {
    const position = this.current();
    if (this.playing && !this.loop && position >= this.duration) {
      this.pause();
      this.onended?.();
    }
    return { position, duration: this.duration, playing: this.playing };
  }
  celebrate() {
    if (!this.context) return;
    [523.25, 659.25, 783.99, 1046.5].forEach((frequency, index) => {
      const at = this.context!.currentTime + index * 0.12;
      const oscillator = this.context!.createOscillator(),
        gain = this.context!.createGain();
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.07, at);
      gain.gain.exponentialRampToValueAtTime(0.001, at + 0.5);
      oscillator.connect(gain).connect(this.context!.destination);
      oscillator.start(at);
      oscillator.stop(at + 0.5);
      oscillator.onended = () => {
        oscillator.disconnect();
        gain.disconnect();
      };
    });
  }
  close() {
    this.abort?.abort();
    this.generation++;
    this.pause();
    this.buffers = [];
    this.context?.close();
    this.context = null;
  }
}
