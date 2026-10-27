"""hyb_tri.py <fit.npz> <out.npz> [with_balls 0|1]: triangulated head (+ UV spheres for the eyeballs) for renders / metrics"""
import sys, numpy as np
Z = np.load(sys.argv[1]); out = sys.argv[2]; wb = len(sys.argv) > 3 and sys.argv[3] == "1"
Q, Fq = Z["V"], Z["Fq"]
F = np.vstack([Fq[:, [0, 1, 2]], Fq[:, [0, 2, 3]]])
V = [Q]; FF = [F]; off = len(Q)
part = [np.zeros(len(F), int)]
if wb:
    for k, (c, R) in enumerate(zip(Z["ball_c"], Z["ball_r"])):
        nu, nvr = 32, 16
        th = np.linspace(0, np.pi, nvr + 1); ph = np.linspace(0, 2 * np.pi, nu, endpoint=False)
        P = []
        for t in th:
            for p in ph:
                P.append([np.sin(t) * np.cos(p), -np.cos(t), np.sin(t) * np.sin(p)])
        P = np.array(P) * R + c
        T = []
        for i in range(nvr):
            for j in range(nu):
                a = i * nu + j; b = i * nu + (j + 1) % nu; c_ = (i + 1) * nu + j; d = (i + 1) * nu + (j + 1) % nu
                T += [[a, c_, b], [b, c_, d]]
        T = np.array(T) + off
        V.append(P); FF.append(T); part.append(np.full(len(T), k + 1)); off += len(P)
np.savez_compressed(out, V=np.vstack(V), F=np.vstack(FF), part=np.concatenate(part))
print("TRI", len(np.vstack(FF)))
