// NapCat 视频取回插件：监听视频消息，用事件里的真实 URL 把视频下载到本地。
// 下载记录写入 videos/latest.json，供外部（麦麦插件）读取。
// 说明：使用 node:https 而非 fetch —— 实测 fetch 在本环境报 "fetch failed"，https 模块稳定。

import fs from "fs";
import path from "path";
import https from "https";
import http from "http";

let logger = null;
const tag = "[video-fetch]";

const writeJson = (p, obj) => {
    try {
        fs.mkdirSync(path.dirname(p), { recursive: true });
        fs.writeFileSync(p, JSON.stringify(obj, null, 2), "utf-8");
    } catch (e) {
        logger?.error(`${tag} writeJson failed: ${e && e.message}`);
    }
};

const download = (url, dest) => new Promise((resolve, reject) => {
    const mod = url.startsWith("https") ? https : http;
    const req = mod.get(url, { headers: { "User-Agent": "Mozilla/5.0" } }, (res) => {
        if (res.statusCode !== 200) {
            res.resume();
            reject(new Error(`HTTP ${res.statusCode}`));
            return;
        }
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        const ws = fs.createWriteStream(dest);
        res.pipe(ws);
        ws.on("finish", () => {
            try { resolve(fs.statSync(dest).size); } catch (e) { reject(e); }
        });
        ws.on("error", reject);
    });
    req.on("error", reject);
    req.setTimeout(90000, () => req.destroy(new Error("timeout")));
});

const handleVideo = async (ctx, seg, event) => {
    const data = seg && seg.data ? seg.data : {};
    const url = data.url || "";
    const name = data.file || `video_${Date.now()}.mp4`;
    if (!url || !/^https?:/i.test(url)) {
        logger?.info(`${tag} skip: url 不是网络地址 (${url.slice(0, 40)})`);
        return;
    }

    const dir = path.join(ctx.dataPath || ".", "videos");
    const record = path.join(dir, "latest.json");
    const dest = path.join(dir, name);

    writeJson(record, {
        phase: "downloading", url, file: name,
        file_size: data.file_size || "",
        group_id: event?.group_id || "", message_id: event?.message_id || "",
        ts: Date.now()
    });

    try {
        const size = await download(url, dest);
        writeJson(record, {
            phase: "done", url, file: name, saved: dest, bytes: size,
            group_id: event?.group_id || "", message_id: event?.message_id || "",
            ts: Date.now()
        });
        logger?.info(`${tag} 下载完成 ${name} ${size} bytes -> ${dest}`);
    } catch (e) {
        writeJson(record, {
            phase: "failed", url, file: name,
            error: String(e && (e.message || e)), ts: Date.now()
        });
        logger?.error(`${tag} 下载失败 ${name}: ${e && (e.message || e)}`);
    }
};

export const plugin_init = async (ctx) => {
    logger = ctx.logger;
    logger.info(`${tag} init, dataPath=${ctx.dataPath}`);
};

export const plugin_onmessage = async (ctx, event) => {
    try {
        logger = logger || ctx.logger;
        const msg = event && event.message;
        if (!Array.isArray(msg)) return;
        for (const seg of msg) {
            if (seg && seg.type === "video") {
                handleVideo(ctx, seg, event).catch(() => { /* logged inside */ });
            }
        }
    } catch (e) {
        logger?.error(`${tag} onmessage error: ${e && (e.stack || e.message)}`);
    }
};
