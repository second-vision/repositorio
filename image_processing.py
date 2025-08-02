# image_processing.py

import cv2
import time
from collections import deque
from ultralytics import YOLO
from paddleocr import PaddleOCR
from spellchecker import SpellChecker
from fuzzywuzzy import fuzz

# Inicializa os modelos
yolo_model = YOLO("yolov8n.pt")
ocr = PaddleOCR(use_angle_cls=True, lang="pt")
spell = SpellChecker(language='pt')

# Lista de objetos permitidos e tradução para português
allowed_objects = ['person', 'bicycle', 'car', 'motorcycle', 'bus', 'train', 'truck', 'traffic light', 'stop sign',
                   'fire hydrant']
translation_dict = {
    'person': 'pessoa', 'bicycle': 'bicicleta', 'car': 'carro', 'motorcycle': 'moto',
    'bus': 'ônibus', 'train': 'trem', 'truck': 'caminhão',
    'traffic light': 'semáforo', 'stop sign': 'placa de pare', 'fire hydrant': 'hidrante'
}

# --- Configurações para Filtragem de Texto ---
MIN_TEXT_SIMILARITY_RATIO = 85
MIN_WORD_COUNT_FOR_MEANINGFUL_TEXT = 2
MIN_AVG_WORD_LENGTH = 2

class ObjectTracker:
    def __init__(self, window_size=5, stability_threshold_ratio=0.6):
        """
        window_size: Número de frames recentes a considerar para estabilidade.
        stability_threshold_ratio: Proporção de frames em window_size em que um objeto
                                   deve estar presente para ser considerado estável (ex: 0.6 para 60%).
        """
        self.history = deque(maxlen=window_size)
        self.window_size = window_size
        # Limiar de contagem para estabilidade: objeto precisa estar em X frames da janela
        self.stability_count_threshold = int(window_size * stability_threshold_ratio)
        if self.stability_count_threshold == 0 and window_size > 0:
            self.stability_count_threshold = 1 # Pelo menos 1 se a proporção for muito baixa mas window > 0

        self.currently_stable_objects = set() # Objetos atualmente considerados estáveis

    def update(self, current_frame_detections_list):
        """
        Atualiza o histórico com as detecções do frame atual e recalcula os objetos estáveis.
        current_frame_detections_list: Uma lista de strings dos objetos detectados no frame.
        """
        self.history.append(set(current_frame_detections_list))

        new_stable_objects = set()
        if not self.history:
            self.currently_stable_objects = new_stable_objects
            return

        possible_objects = set()
        for frame_set in self.history:
            possible_objects.update(frame_set)

        for obj_name in possible_objects:
            count = 0
            for frame_set in self.history:
                if obj_name in frame_set:
                    count += 1
            if count >= self.stability_count_threshold:
                new_stable_objects.add(obj_name)
        
        self.currently_stable_objects = new_stable_objects

    def get_stable_objects(self):
        """
        Retorna a lista de objetos atualmente considerados estáveis.
        """
        return list(self.currently_stable_objects)


def is_text_meaningful(text_list):
    if not text_list:
        return []
    full_text = " ".join(text_list)
    words = full_text.split()
    if not words:
        return []
    if len(words) < MIN_WORD_COUNT_FOR_MEANINGFUL_TEXT:
        # print(f"Texto descartado (poucas palavras): '{full_text}'")
        return []
    avg_word_len = sum(len(word) for word in words) / len(words)
    if avg_word_len < MIN_AVG_WORD_LENGTH:
        # print(f"Texto descartado (palavras muito curtas/avg): '{full_text}'")
        return []
    return text_list


class TextStabilizer:
    def __init__(self, similarity_threshold=MIN_TEXT_SIMILARITY_RATIO, stability_count=3):
        self.similarity_threshold = similarity_threshold  # Limiar para considerar textos "iguais"
        self.stability_count = stability_count          # Quantas vezes um candidato precisa ser visto
        
        self.current_candidate_text = ""      # O texto que está atualmente sendo "observado"
        self.current_candidate_counter = 0    # Contador para o current_candidate_text
        self.last_effectively_sent_text = None # O texto conceitual que foi enviado por último (pode ter pequenas variações)
        
    

    def update(self, new_text_raw):
      
        new_text_cleaned = " ".join(new_text_raw.split())
        

        if new_text_cleaned: # Se há um novo texto detectado
            # Compara o novo texto com o candidato atual
            if self.current_candidate_text and \
               fuzz.ratio(new_text_cleaned.lower(), self.current_candidate_text.lower()) >= self.similarity_threshold:
                # O novo texto é similar ao candidato atual, então incrementa o contador do candidato
                self.current_candidate_counter += 1
              
            else:
                # O novo texto é DIFERENTE do candidato atual (ou não havia candidato)
                # O novo texto se torna o novo candidato
                self.current_candidate_text = new_text_cleaned
                self.current_candidate_counter = 1
               
        else: # Novo texto detectado é vazio
            # Se havia um candidato, ele "desaparece"
            if self.current_candidate_text:
               
                self.current_candidate_text = ""
                self.current_candidate_counter = 0
           
                pass

        # --- Lógica de Decisão de Envio ---
        text_to_output = None

        if self.current_candidate_text and self.current_candidate_counter >= self.stability_count:
            # O candidato atual atingiu a contagem de estabilidade
           

            # Agora, verifica se este candidato estável é conceitualmente DIFERENTE do último texto enviado
            if self.last_effectively_sent_text is None or \
               fuzz.ratio(self.current_candidate_text.lower(), self.last_effectively_sent_text.lower()) < self.similarity_threshold:
                # É o primeiro envio OU o candidato estável é suficientemente diferente do último enviado
                text_to_output = self.current_candidate_text
                self.last_effectively_sent_text = self.current_candidate_text # Armazena a forma EXATA que foi enviada
               
                pass
        elif not self.current_candidate_text: # Não há candidato atual (ex: texto desapareceu da cena)
            if self.last_effectively_sent_text is not None and self.last_effectively_sent_text != "":
                # Havia um texto enviado anteriormente, e agora não há mais nada. Envia string vazia.
               
                text_to_output = ""
                self.last_effectively_sent_text = "" #
            
                pass
       
            pass
            
        return text_to_output


def get_objects_from_cloud_api(frame):
    """
    Placeholder para a função que envia um frame para uma API na nuvem.
    Simula latência de rede e retorna dados em um formato compatível.
    """
    print("[API] Internet detectada. Chamando API de detecção de objetos na nuvem...")
    
    # Futuramente, aqui você faria a chamada real usando 'requests' ou outra lib.
    # Ex: response = requests.post("https://sua.api/detect", files={'image': frame_bytes})
    # mock_api_results = response.json()['objects']
    
    # Simula a latência da rede
    time.sleep(0.5) 
    
    # Simula uma resposta bem-sucedida da API.
    # O importante é retornar uma lista de nomes de objetos (strings) como o YOLO faria.
    mock_api_results = ['car', 'person', 'traffic light']
    print(f"[API] Resposta simulada recebida: {mock_api_results}")
    
    return mock_api_results


def camera_capture_loop(characteristic_objects, characteristic_texts, shared_state):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Erro ao acessar a câmera")
        return

    # Ajuste os parâmetros do ObjectTracker conforme necessário:
    # window_size: quantos frames recentes considerar para estabilidade.
    # stability_threshold_ratio: que proporção desses frames um objeto precisa aparecer.
    # Ex: window_size=5, stability_threshold_ratio=0.6 => estável se aparecer em 3 de 5 frames.
    tracker = ObjectTracker(window_size=5, stability_threshold_ratio=0.6)
    
    text_stabilizer = TextStabilizer(similarity_threshold=MIN_TEXT_SIMILARITY_RATIO, stability_count=3)

    frame_count = 0
    PROCESS_EVERY_N_FRAMES = 2  # Processa YOLO a cada 2 frames capturados (ajuste se precisar de menos carga)
    OCR_PROCESS_EVERY_N_FRAMES = 1  # Processa OCR a cada 2 frames que o YOLO processou

    last_sent_objects_str = None
    ocr_yolo_cycle_count = 0 # Contador para ciclos de YOLO, para controlar frequência de OCR
    perform_ocr_correction = True # Mantenha False para melhor performance, True para correção

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Falha ao capturar imagem")
            time.sleep(0.5)
            continue

        frame_count += 1
        process_this_yolo_frame = (frame_count % PROCESS_EVERY_N_FRAMES == 0)

        if not process_this_yolo_frame:
            if PROCESS_EVERY_N_FRAMES > 1: # Só dorme se estiver pulando frames YOLO
                time.sleep(0.01)
            continue
        
        if frame_count > 1000 * PROCESS_EVERY_N_FRAMES : # Reset genérico para frame_count
             frame_count = 0


        # --- Processamento de Objetos (YOLO) ---
        detected_objects_in_current_frame_list = []

        # AQUI ESTÁ A LÓGICA DE DECISÃO
        if shared_state.get('internet_connected', False):
            # Se a internet está conectada, usa a API da nuvem
            detected_objects_in_current_frame_list = get_objects_from_cloud_api(frame)
        else:
            yolo_results = yolo_model(frame, verbose=False)
            
            for r in yolo_results:
                for c in r.boxes.cls:
                    obj_name = r.names[int(c)]
                    if obj_name in allowed_objects:
                        detected_objects_in_current_frame_list.append(translation_dict.get(obj_name, obj_name))
        
        tracker.update(detected_objects_in_current_frame_list)

        stable_objects_list = tracker.get_stable_objects()
        stable_objects_list.sort() # IMPORTANTE: Ordenar para string consistente  
        current_objects_str = ", ".join(stable_objects_list) if stable_objects_list else "none"
      
        if current_objects_str != last_sent_objects_str:
            characteristic_objects.send_update(current_objects_str)
            last_sent_objects_str = current_objects_str
            # print(f"DEBUG Objects: {current_objects_str}")


        # --- Processamento de Texto (OCR) ---
        final_text_to_send = None
        ocr_yolo_cycle_count +=1 
        
        if ocr_yolo_cycle_count >= OCR_PROCESS_EVERY_N_FRAMES:
            ocr_yolo_cycle_count = 0 # Reseta o contador de ciclo para OCR

            ocr_raw_paddle_results = ocr.ocr(frame, cls=True)
            extracted_texts_phrases = []

            if ocr_raw_paddle_results:
                for line_idx in range(len(ocr_raw_paddle_results)):
                    line_data = ocr_raw_paddle_results[line_idx]
                    if line_data:
                        phrase_words = []
                        for word_info in line_data:
                            text_from_ocr = word_info[1][0]
                            if perform_ocr_correction:
                                corrected = spell.correction(text_from_ocr)
                                phrase_words.append(corrected if corrected else text_from_ocr)
                            else:
                                phrase_words.append(text_from_ocr)
                        
                        meaningful_phrase_words = is_text_meaningful(phrase_words)
                        if meaningful_phrase_words:
                            extracted_texts_phrases.append(" ".join(meaningful_phrase_words))
            
            current_raw_text_from_frame = " | ".join(extracted_texts_phrases) if extracted_texts_phrases else ""
            
            stabilized_text = text_stabilizer.update(current_raw_text_from_frame)
            if stabilized_text is not None:
                final_text_to_send = stabilized_text
        
        if final_text_to_send:
            characteristic_texts.send_update(final_text_to_send)
            # print(f"DEBUG Text: {final_text_to_send}")

    cap.release()
