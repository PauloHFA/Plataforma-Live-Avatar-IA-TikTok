# Camadas do Avatar 2D (`godot/assets/avatar/`)

Para substituir a arte inicial do avatar sem alterar código, substitua os PNGs mantendo o mesmo canvas transparente (`600x760` RGBA, alinhado ao centro):

| Arquivo | Camada | Obrigatório |
| --- | --- | --- |
| `back_hair.png` | Cabelo traseiro (atrás do tronco e pescoço) | Opcional |
| `body.png` | Tronco, ombros e pescoço | Sim (fallback geométrico se ausente) |
| `head.png` | Rosto e cabelo frontal | Sim (fallback geométrico se ausente) |
| `eyes_open.png` | Olhos abertos | Sim para sprites |
| `eyes_closed.png` | Olhos fechados (piscada automática) | Opcional |
| `eyes_happy.png` | Expressão feliz (`happy`) | Opcional |
| `mouth_sil.png` | Boca em repouso / `PP` | Sim para sprites |
| `mouth_aa.png` | Viseme `aa` | Opcional |
| `mouth_ee.png` | Viseme `E` | Opcional |
| `mouth_ih.png` | Viseme `ih` | Opcional |
| `mouth_oh.png` | Viseme `oh` | Opcional |
| `mouth_ou.png` | Viseme `ou` | Opcional |

Se `body.png` ou `head.png` não estiverem presentes, o `AvatarController` ativa automaticamente o `FallbackRig` geométrico.
