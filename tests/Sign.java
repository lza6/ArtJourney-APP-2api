import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 逐行复刻 ArtJourney v3.2.4 的签名逻辑（w0.g.d + w0.g.c），用于交叉校验 Python 实现。
 * 参数以 "k=v" 形式传入，输出 signature（HMAC-SHA256 hex, lowercase）。
 */
public final class Sign {

    private static final char[] HEX = "0123456789abcdef".toCharArray();

    static String hmacHex(String key, String message) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key.getBytes("UTF-8"), "HmacSHA256"));
        byte[] digest = mac.doFinal(message.getBytes("UTF-8"));
        char[] out = new char[digest.length * 2];
        for (int i = 0; i < digest.length; i++) {
            int v = digest[i] & 0xFF;
            out[i * 2] = HEX[v >>> 4];
            out[i * 2 + 1] = HEX[v & 0x0F];
        }
        return new String(out);
    }

    static String canonical(String prompt, String ratio, String style,
                            String seedId, String steps, String cfgScale, String negativePrompt) {
        StringBuilder sb = new StringBuilder();
        sb.append("prompt=").append(prompt).append("&");
        sb.append("ratio=").append(ratio).append("&");
        sb.append("style=").append(style).append("&");
        if (seedId != null) sb.append("seed_id=").append(seedId).append("&");
        if (steps != null) sb.append("steps=").append(steps).append("&");
        if (cfgScale != null) sb.append("cfg_scale=").append(cfgScale).append("&");
        if (negativePrompt != null) sb.append("negative_prompt=").append(negativePrompt).append("&");
        String b13 = sb.toString();
        if (b13.isEmpty() || b13.charAt(b13.length() - 1) != '&') {
            return "";
        }
        // r.y(b13, "&", 6) == lastIndexOf("&", lastIndex) == length-1
        int idx = b13.lastIndexOf("&", b13.length() - 1);
        String head = b13.substring(0, idx);
        String tail = b13.substring(idx + 1);
        return head + "" + tail;
    }

    public static void main(String[] args) throws Exception {
        String key = args[0];
        if (args.length > 1 && "--file".equals(args[1])) {
            // 从 UTF-8 文件读取用例（规避 Windows 控制台 GBK 参数编码问题）
            java.nio.file.Path path = java.nio.file.Paths.get(args[2]);
            for (String line : java.nio.file.Files.readAllLines(path, java.nio.charset.StandardCharsets.UTF_8)) {
                if (line.isBlank() || line.startsWith("#")) continue;
                String[] f = line.split("\t", -1);
                String msg = canonical(f[0], f[1], f[2], nullIfDash(f[3]), nullIfDash(f[4]), nullIfDash(f[5]), nullIfDash(f[6]));
                System.out.println(msg + "\t" + hmacHex(key, msg));
            }
            return;
        }
        String prompt = args[1];
        String ratio = args[2];
        String style = args[3];
        String seedId = nullIfDash(args.length > 4 ? args[4] : "-");
        String steps = nullIfDash(args.length > 5 ? args[5] : "-");
        String cfgScale = nullIfDash(args.length > 6 ? args[6] : "-");
        String negative = nullIfDash(args.length > 7 ? args[7] : "-");
        String msg = canonical(prompt, ratio, style, seedId, steps, cfgScale, negative);
        System.out.println(msg);
        System.out.println(hmacHex(key, msg));
    }

    private static String nullIfDash(String v) {
        return "-".equals(v) ? null : v;
    }
}
