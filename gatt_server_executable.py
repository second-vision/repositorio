from __future__ import print_function
import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
import array
import sys
import gi
import time 


try:
    from gi.repository import GLib
except ImportError:
    print("Erro ao importar o GLib")
import advertising
import gatt_server
import argparse
import threading


import image_processing
import battery_monitor

# Objeto de estado compartilhado que será passado para os threads.
# Dicionários são mutáveis, então as mudanças feitas em um thread serão visíveis nos outros.
shared_state = {'internet_connected': False}
internet_check_event = threading.Event()


def internet_status_updater_loop(state, event):
    """
    Thread que verifica a conexão com a internet periodicamente e atualiza o estado compartilhado.
    """
    while True:
        status = gatt_server.is_internet_available() 
        if status != state.get('internet_connected'):
            print(f"[Internet Check] Status da internet mudou para: {'Conectado' if status else 'Desconectado'}")
            state['internet_connected'] = status
        
        # AQUI ESTÁ A MUDANÇA:
        # Em vez de time.sleep(15), esperamos pelo evento por 15 segundos.
        # Se o evento for sinalizado (event.set() for chamado), a espera termina imediatamente.
        # Se não, ela termina após 15 segundos (timeout).
        print("[Internet Check] Aguardando 15s ou sinal para nova verificação...")
        event.wait(timeout=15)
        # Limpa o sinal para a próxima iteração.
        event.clear()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--adapter-name', type=str, help='Adapter name', default='')
    args = parser.parse_args()
    adapter_name = args.adapter_name

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    mainloop = GLib.MainLoop()

    advertising.advertising_main(mainloop, bus, adapter_name)
    app = gatt_server.gatt_server_main(mainloop, bus, adapter_name, internet_check_event)

    if not app:
        print("Aplicação GATT não foi inicializada corretamente devido a erro no INA219.")
        sys.exit(1)

    yolo_characteristic = app.services[0].characteristics[0]
    ocr_characteristic = app.services[0].characteristics[1]
    battery_characteristic = app.services[0].characteristics[3]

    # Start camera capture loop in a separate thread
    # Inicia o thread de processamento de imagem, passando o estado compartilhado
    camera_thread = threading.Thread(
        target=image_processing.camera_capture_loop, 
        args=(yolo_characteristic, ocr_characteristic, shared_state) # Passa o shared_state
    )
    camera_thread.daemon = True # Garante que o thread termine se o principal terminar
    camera_thread.start()

    # Start battery monitor loop in a separate thread
    battery_thread = threading.Thread(target=battery_monitor.battery_monitor_loop, args=(battery_characteristic,))
    battery_thread.daemon = True
    battery_thread.start()

    # Inicia o NOVO thread para verificar o status da internet
    internet_thread = threading.Thread(target=internet_status_updater_loop, args=(shared_state, internet_check_event))
    internet_thread.daemon = True
    internet_thread.start()

    try:
        mainloop.run()
    except KeyboardInterrupt:
        print("Program terminated")


if __name__ == '__main__':
    main()
