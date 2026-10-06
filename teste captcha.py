import cv2
import numpy as np
import torch
import torchvision.transforms as T
import torchvision.models as models
from scipy.optimize import linear_sum_assignment

# 1. Modelo para extração de características (ResNet18 pré-treinada)
model = models.resnet18(pretrained=True)
# Remove a camada de classificação final para obter o vetor de características (embedding)
feature_extractor = torch.nn.Sequential(*list(model.children())[:-1])
feature_extractor.eval()

# Transformações de pré-processamento padronizadas
preprocess = T.Compose([
    T.ToPILImage(),
    T.Resize((224, 224)),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

def get_image_embedding(crop_img):
    """Gera um vetor numérico que representa a estrutura visual do ícone."""
    # Converte BGR (OpenCV) para RGB
    crop_rgb = cv2.cvtColor(crop_img, cv2.COLOR_BGR2RGB)
    tensor = preprocess(crop_rgb).unsqueeze(0)
    with torch.no_grad():
        embedding = feature_extractor(tensor).squeeze().numpy()
    # Normalização L2
    return embedding / np.linalg.norm(embedding)

def extract_slots(captcha_img):
    """
    Recorta os 3 slots superiores e 3 inferiores baseados em coordenadas relativas da janela.
    Ajuste os valores das Bounding Boxes conforme a resolução do seu cliente/janela.
    """
    h, w, _ = captcha_img.shape
    
    # Exemplo de coordenadas relativas (ajuste conforme a posição real na tela)
    top_slots = [
        captcha_img[int(h*0.22):int(h*0.48), int(w*0.05):int(w*0.33)],
        captcha_img[int(h*0.22):int(h*0.48), int(w*0.36):int(w*0.64)],
        captcha_img[int(h*0.22):int(h*0.48), int(w*0.67):int(w*0.95)]
    ]
    bottom_slots = [
        captcha_img[int(h*0.52):int(h*0.78), int(w*0.05):int(w*0.33)],
        captcha_img[int(h*0.52):int(h*0.78), int(w*0.36):int(w*0.64)],
        captcha_img[int(h*0.52):int(h*0.78), int(w*0.67):int(w*0.95)]
    ]
    return top_slots, bottom_slots

def resolve_captcha(captcha_path):
    img = cv2.imread(captcha_path)
    top_crops, bottom_crops = extract_slots(img)
    
    # Extrai embeddings para topo e base
    top_embeds = [get_image_embedding(crop) for crop in top_crops]
    bottom_embeds = [get_image_embedding(crop) for crop in bottom_crops]
    
    # Monta a matriz de similaridade Cosseno (3x3)
    cost_matrix = np.zeros((3, 3))
    for i in range(3):
        for j in range(3):
            # Distância de cosseno (1 - similaridade)
            sim = np.dot(top_embeds[i], bottom_embeds[j])
            cost_matrix[i, j] = 1.0 - sim

    # Algoritmo Húngaro para encontrar a combinação perfeita
    row_ind, col_ind = linear_sum_assignment(cost_matrix)
    
    pairs = []
    for top_idx, bot_idx in zip(row_ind, col_ind):
        pairs.append((top_idx, bot_idx))
        print(f"Top Slot {top_idx + 1} corresponde ao Bottom Slot {bot_idx + 1}")
        
    return pairs

# Teste de execução
pairs = resolve_captcha("captcha.png")