// NapCat 视频取回插件：监听视频消息，用事件里的真实 URL 把视频下载到本地。
// 下载记录写入 videos/latest.json，供外部（麦麦插件）读取。
// 说明：使用 node:https 而非 fetch —— 实测 fetch 在本环境报 "fetch failed"，https 模块稳定。

import fs from "fs";
import path from "path";
import https from "https";
import http from "http";
import dns from "dns";

let logger = null;
const tag = "[video-fetch]";

// ---- 下载目标安全校验（防 SSRF）----
// 事件里的 data.url 可能受消息内容影响，不能只要 http/https 就下。
// 两道：域名必须在 QQ 媒体白名单内；解析后的 IP 不得是内网/元数据地址。
const ALLOWED_HOST_SUFFIX = [
    ".qq.com", ".qq.com.cn", ".gtimg.com", ".qpic.cn", ".tencent.com",
];

const MAX_BYTES = 500 * 1024 * 1024;

const hostAllowed = (host) => {
    const h = String(host || "").toLowerCase();
    if (!h) return false;
    return ALLOWED_HOST_SUFFIX.some((s) => h === s.slice(1) || h.endsWith(s));
};

const ipIsPrivate = (ip) => {
    const v = String(ip || "").toLowerCase();
    if (!v) return true;
    if (v.includes(":")) {
        return v === "::1" || v === "::" ||
            v.startsWith("fe80") || v.startsWith("fc") || v.startsWith("fd");
    }
    const p = v.split(".").map(Number);
    if (p.length !== 4 || p.some((n) => !Number.isInteger(n))) return true;
    const [a, b] = p;
    if (a === 0 || a === 10 || a === 127) return true;
    if (a === 169 && b === 254) return true;          // link-local / 云元数据
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 100 && b >= 64 && b <= 127) return true; // CGNAT
    if (a >= 224) return true;                        // 组播/保留
    return false;
};

const assertSafeUrl = async (rawUrl) => {
    let u;
    try {
        u = new URL(String(rawUrl));
    } catch (_) {
        throw new Error("url 解析失败");
    }
    if (u.protocol !== "https:" && u.protocol !== "http:") {
        throw new Error(`非 http(s) 协议: ${u.protocol}`);
    }
    if (!hostAllowed(u.hostname)) {
        throw new Error(`域名不在白名单: ${u.hostname}`);
    }
    const addrs = await dns.promises.lookup(u.hostname, { all: true });
    for (const a of addrs) {
        if (ipIsPrivate(a.address)) {
            throw new Error(`解析到内网地址: ${a.address}`);
        }
    }
    return u;
};

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
        const declared = Number(res.headers["content-length"] || 0);
        if (declared && declared > MAX_BYTES) {
            res.resume();
            reject(new Error(`内容过大: ${declared} bytes`));
            return;
        }
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        const ws = fs.createWriteStream(dest);
        let written = 0;
        res.on("data", (chunk) => {
            written += chunk.length;
            if (written > MAX_BYTES) {
                req.destroy(new Error(`内容过大: 超过 ${MAX_BYTES} bytes`));
            }
        });
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
    const name = data.file || data.filename || `video_${Date.now()}.mp4`;

    // 诊断：把视频段的完整字段打出来，便于处理转发消息等特殊情况
    try {
        logger?.info(`${tag} 视频段字段: ${JSON.stringify(data).slice(0, 600)}`);
    } catch (_) { /* ignore */ }

    if (!url || !/^https?:/i.test(url)) {
        logger?.info(`${tag} skip: url 不是网络地址 (${String(url).slice(0, 80)})`);
        return;
    }

    const dir = path.join(ctx.dataPath || ".", "videos");
    const record = path.join(dir, "latest.json");
    const dest = path.join(dir, path.basename(name));

    // 安全校验：非白名单域名 / 解析到内网 / 非 http(s)，一律拒绝下载
    try {
        await assertSafeUrl(url);
    } catch (e) {
        logger?.error(`${tag} 拒绝下载（安全校验）: ${e && e.message} url=${String(url).slice(0, 120)}`);
        writeJson(record, {
            phase: "rejected", url, file: path.basename(name),
            reason: String(e && e.message), ts: Date.now()
        });
        return;
    }

    writeJson(record, {
        phase: "downloading", url, file: path.basename(name),
        file_size: data.file_size || "",
        group_id: event?.group_id || "", message_id: event?.message_id || "",
        ts: Date.now()
    });

    try {
        const size = await download(url, dest);
        writeJson(record, {
            phase: "done", url, file: path.basename(name), saved: dest, bytes: size,
            group_id: event?.group_id || "", message_id: event?.message_id || "",
            ts: Date.now()
        });
        logger?.info(`${tag} 下载完成 ${name} ${size} bytes -> ${dest}`);
    } catch (e) {
        writeJson(record, {
            phase: "failed", url, file: path.basename(name),
            error: String(e && (e.message || e)), ts: Date.now()
        });
        logger?.error(`${tag} 下载失败 ${name}: ${e && (e.message || e)}`);
    }
};

// 递归查找消息（含多层合并转发）里的视频段
// 加两层护栏：深度上限 + 去重，避免互转消息导致无限递归
const collectVideos = (segments, out, depth = 0, seen = new Set()) => {
    if (!Array.isArray(segments) || depth > 8) return;
    for (const seg of segments) {
        if (!seg || typeof seg !== "object") continue;
        if (seg.type === "video") {
            out.push(seg);
        } else if (seg.type === "forward" || seg.type === "node") {
            const d = seg.data || {};
            const inner = d.content || d.message;
            // 优先用实体作去重键；拿不到就退化成内容哈希
            let key = String(d.id || d.file || "");
            if (!key) {
                try { key = JSON.stringify(inner).slice(0, 200); } catch (_) { key = ""; }
            }
            if (key && seen.has(key)) continue;
            if (key) seen.add(key);
            if (Array.isArray(inner)) collectVideos(inner, out, depth + 1, seen);
        }
    }
};

export const plugin_init = async (ctx) => {
    logger = ctx.logger;
    logger.info(`${tag} init, dataPath=${ctx.dataPath}`);
    try {
        const ak = ctx.actions ? Object.keys(ctx.actions) : [];
        logger.info(`${tag} ctx 键: ${Object.keys(ctx).join(",")}`);
        logger.info(`${tag} actions 键（前 40）: ${ak.slice(0, 40).join(",")}`);
    } catch (e) {
        logger.info(`${tag} 探测 actions 失败: ${e && e.message}`);
    }
};

export const plugin_onmessage = async (ctx, event) => {
    try {
        logger = logger || ctx.logger;
        const msg = event && event.message;
        if (!Array.isArray(msg)) {
            logger?.info(`${tag} 事件无 message 数组，post=${event?.post_type} keys=${Object.keys(event || {}).join(",")}`);
            return;
        }
        // 诊断：打印本条消息的段类型分布（便于定位合并转发等特殊情况）
        const types = msg.map((s) => (s && s.type) || "?").join(",");
        logger?.info(`${tag} 段类型=[${types}] post=${event?.post_type} gid=${event?.group_id}`);
        if (types.includes("forward") || types.includes("node")) {
            logger?.info(`${tag} 合并转发原文: ${JSON.stringify(msg).slice(0, 1500)}`);
        }
        const videos = [];
        collectVideos(msg, videos);
        if (videos.length) {
            logger?.info(`${tag} 找到 ${videos.length} 个视频段`);
        }
        for (const seg of videos) {
            handleVideo(ctx, seg, event).catch(() => { /* logged inside */ });
        }
    } catch (e) {
        logger?.error(`${tag} onmessage error: ${e && (e.stack || e.message)}`);
    }
};
