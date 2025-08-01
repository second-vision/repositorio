import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
import functools
import smbus  # Use smbus2 para evitar problemas
import time
import threading
import exceptions
import adapters
import os
import uuid  # Import para gerar UUIDs únicos
from INA219 import INA219
from collections import deque
import json # Para processar JSON
import subprocess


BLUEZ_SERVICE_NAME = 'org.bluez'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'

LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
GATT_DESC_IFACE = 'org.bluez.GattDescriptor1'

WIFI_CREDENTIALS_FILE = "/etc/wifi_credentials_received.json"


try:
    ina219 = INA219(addr=0x42)
except Exception as e:
    print(f"Erro ao inicializar INA219: {e}")

class Application(dbus.service.Object):
    def __init__(self, bus, connection_event):
        self.path = '/'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)
        service = TestService(bus, 0, ina219, connection_event)
        self.add_service(service)
        # Armazena uma referência direta à característica de Wi-Fi
        self.wifi_status_characteristic = service.characteristics[4]

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        response = {}
        print('GetManagedObjects')
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            chrcs = service.get_characteristics()
            for chrc in chrcs:
                response[chrc.get_path()] = chrc.get_properties()
                descs = chrc.get_descriptors()
                for desc in descs:
                    response[desc.get_path()] = desc.get_properties()
        return response


class Service(dbus.service.Object):
    PATH_BASE = '/org/bluez/example/service'

    def __init__(self, bus, index, uuid_str, primary):
        unique_id = str(uuid.uuid4())[:8]  # Gera um identificador único de 8 caracteres
        self.path = f"{self.PATH_BASE}{index}_{unique_id}"
        self.bus = bus
        self.uuid = uuid_str
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                'UUID': self.uuid,
                'Primary': self.primary,
                'Characteristics': dbus.Array(
                    self.get_characteristic_paths(),
                    signature='o')
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    def get_characteristic_paths(self):
        result = []
        for chrc in self.characteristics:
            result.append(chrc.get_path())
        return result

    def get_characteristics(self):
        return self.characteristics

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise exceptions.InvalidArgsException()
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + '/char' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags
        self.descriptors = []
        self.value = []
        self.notifying = False
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                'Service': self.service.get_path(),
                'UUID': self.uuid,
                'Flags': self.flags,
                'Descriptors': dbus.Array(
                    self.get_descriptor_paths(),
                    signature='o')
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_descriptor(self, descriptor):
        self.descriptors.append(descriptor)

    def get_descriptor_paths(self):
        result = []
        for desc in self.descriptors:
            result.append(desc.get_path())
        return result

    def get_descriptors(self):
        return self.descriptors

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise exceptions.InvalidArgsException()
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE,
                         in_signature='a{sv}',
                         out_signature='ay')
    def ReadValue(self, options):
        print('TestCharacteristic Read: ' + repr(self.value))
        return self.value

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        if self.notifying:
            return
        self.notifying = True
        self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': self.value}, [])

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        if not self.notifying:
            return
        self.notifying = False

    @dbus.service.signal(DBUS_PROP_IFACE,
                         signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def send_update(self, value):
        self.set_value(value)
        if self.notifying:
            self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': self.value}, [])

    def set_value(self, value):
        self.value = [dbus.Byte(ord(c)) for c in value]
        if self.notifying:
            self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': self.value}, [])


class TestService(Service):
    TEST_SVC_UUID = '12345678-1234-5678-1234-56789abcdef0'

    def __init__(self, bus, index, ina219, connection_event):
        Service.__init__(self, bus, index, self.TEST_SVC_UUID, True)
        self.add_characteristic(YoloCharacteristic(bus, 0, self))
        self.add_characteristic(OcrPaddle(bus, 1, self))
        self.add_characteristic(ShutdownCharacteristic(bus, 2, self))
        self.add_characteristic(BatteryCharacteristic(bus, 3, self, ina219))
        self.add_characteristic(WifiStatusCharacteristic(bus, 4, self))
        self.add_characteristic(WifiCommandCharacteristic(bus, 5, self, connection_event))


class YoloCharacteristic(Characteristic):
    YOLO_CHRC_UUID = '12345678-1234-5678-1234-56789abcdef1'

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index,
            self.YOLO_CHRC_UUID,
            ['read', 'notify'],
            service)

        # self.value = [dbus.Byte(ord(c)) for c in 'Start']


class OcrPaddle(Characteristic):
    PADDLE_CHRC_UUID = '12345678-1234-5678-1234-56789abcdef2'

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index,
            self.PADDLE_CHRC_UUID,
            ['read', 'notify'],
            service)

        # self.value = [dbus.Byte(ord(c)) for c in 'Start']


class BatteryCharacteristic(Characteristic):
    BATTERY_CHRC_UUID = '12345678-1234-5678-1234-56789abcdef4'

    def __init__(self, bus, index, service, ina219_instance):
        Characteristic.__init__(
            self, bus, index,
            self.BATTERY_CHRC_UUID,
            ['read', 'notify'],
            service)
        self.ina219 = ina219_instance
        # Para o buffer de corrente média (opcional, mas recomendado para tempo restante)
        self.current_buffer = deque(maxlen=60) # Armazena ~1 minuto de leituras se atualizado a cada segundo
        self.nominal_capacity_mah = 5200 # Capacidade total nominal em mAh

        initial_info_str = self._get_formatted_battery_string()

        self.value = [dbus.Byte(b) for b in initial_info_str.encode('utf-8')]


    def _get_current_status_and_percentage(self):
        """Lê o sensor e calcula a porcentagem."""
        if not self.ina219: # Adiciona uma verificação se o sensor não foi inicializado
            print("Warning: INA219 sensor not available in BatteryCharacteristic.")
            return 0.0, 0.0, True # voltage, current, error
        
        try:
            bus_voltage = self.ina219.getBusVoltage_V()
            current_mA = self.ina219.getCurrent_mA()

            min_voltage = 6.0  # Ajuste se a tensão de corte do seu UPS for diferente
            max_voltage = 8.4  # Tensão de 2S LiPo/Li-ion totalmente carregada
            
            if bus_voltage <= min_voltage:
                percentage = 0.0
            elif bus_voltage >= max_voltage:
                percentage = 100.0
            else:
                percentage = (bus_voltage - min_voltage) / (max_voltage - min_voltage) * 100.0
            
            percentage = max(0.0, min(100.0, percentage)) # Garante 0-100%
            return bus_voltage, current_mA, percentage, False # voltage, current, percentage, error
        except Exception as e:
            print(f"Error reading INA219 in BatteryCharacteristic: {e}")
            return 0.0, 0.0, 0.0, True

    def _update_current_buffer(self, current_mA):
        """Atualiza o buffer de corrente de descarga."""
        # Assumindo que corrente de descarga é > 0, e carga é < 0 (ou vice-versa)
        # Se seu getCurrent_mA() retorna negativo para descarga:
        if current_mA < -10: # Considera descarga se for significativamente negativa
            self.current_buffer.append(abs(current_mA))
        elif current_mA > 10: # Considera carga
            self.current_buffer.clear()
            pass # Não adiciona corrente de carga ao buffer de descarga


    def _get_average_discharge_current_mA(self):
        """Calcula a corrente de descarga média do buffer."""
        if not self.current_buffer:
            return 0.0
        return sum(self.current_buffer) / len(self.current_buffer)

    def _calculate_remaining_time_hours(self, percentage, avg_discharge_current_mA):
        if avg_discharge_current_mA < 10: # Se corrente de descarga média muito baixa (ou carregando/idle)
            return float('inf') 
        
        remaining_capacity_mAh = (percentage / 100.0) * self.nominal_capacity_mah
        
        if remaining_capacity_mAh <= 0:
             return 0.0

        estimated_time_hours = remaining_capacity_mAh / avg_discharge_current_mA
        return estimated_time_hours

    def _format_time(self, time_hours):
        """Converte horas decimais para formato 'Xh Ymin' ou similar."""
        if time_hours == float('inf'):
             _, current_mA, _, _ = self._get_current_status_and_percentage()
             if current_mA > 10 : # Corrente positiva, assumindo que é carga
                 return "Carregando"
             if not self.current_buffer: # Ainda não há dados suficientes para média
                 return "Calculando..."
             return "Completo" # Ou "N/A" se corrente de descarga muito baixa e não carregando

        if time_hours <= 0: # Menos que zero ou zero
            return "Descarregado" if time_hours < (1/60) else "< 1 min" # Se for muito próximo de zero mas positivo

        hours = int(time_hours)
        minutes = int((time_hours * 60) % 60)

        if hours == 0:
            if minutes < 1 : return "< 1 min"
            return f"{minutes}min"
        else:
            return f"{hours}h {minutes}min"

    def _get_formatted_battery_string(self):
        """Obtém todos os dados da bateria e formata a string de saída."""
        bus_voltage, current_mA, percentage, error = self._get_current_status_and_percentage()

        if error:
            return "Bateria: Erro Leitura"

        self._update_current_buffer(current_mA) # Atualiza buffer com a corrente instantânea
        avg_discharge_current_mA = self._get_average_discharge_current_mA()
        
        estimated_time_hours = self._calculate_remaining_time_hours(percentage, avg_discharge_current_mA)
        formatted_time_str = self._format_time(estimated_time_hours)

        # Ex: "75.3%, 6h 30min"
        return f"{percentage:.1f}%, {formatted_time_str}"


    @dbus.service.method(GATT_CHRC_IFACE,
                         in_signature='a{sv}',
                         out_signature='ay')
    def ReadValue(self, options):
        battery_info_str = self._get_formatted_battery_string()
        current_value_bytes = [dbus.Byte(b) for b in battery_info_str.encode('utf-8')]
        return dbus.Array(current_value_bytes, signature='y')


    def send_battery_update(self):
        # Este método é chamado periodicamente (ex: pelo battery_monitor_loop)
        battery_info_str = self._get_formatted_battery_string()
        self.send_update(battery_info_str) # Chama o send_update da classe base


class ShutdownCharacteristic(Characteristic):
    SHUTDOWN_CHRC_UUID = '12345678-1234-5678-1234-56789abcdef3'

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index,
            self.SHUTDOWN_CHRC_UUID,
            ['write'],
            service)
        self.value = []

    @dbus.service.method(GATT_CHRC_IFACE,
                         in_signature='aya{sv}',
                         out_signature='ay')
    def WriteValue(self, value, options):
        print('Shutdown command received, shutting down...')
        self.value = value
        # Encerra a aplicação GATT
        os.system('sudo systemctl stop bluetooth')
        # Desliga o sistema operacional
        os.system('sudo shutdown now')

class WifiStatusCharacteristic(Characteristic):
    WIFI_STATUS_UUID = '12345678-1234-5678-1234-56789abcdef5' 

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index,
            self.WIFI_STATUS_UUID,
            ['read', 'notify'],
            service)
        self.last_known_status_str = "Inicializando..."
    
    def update_and_notify_status(self):
        """
        Verifica o status da internet e, se mudou, envia uma notificação.
        """
        # Esta lógica é a mesma do seu ReadValue atual
        try:
            cmd = ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            active_ssid = None
            for line in result.stdout.strip().split('\n'):
                if line.startswith('yes:'):
                    active_ssid = line.split(':', 1)[1]
                    break
            
            current_status_str = f"Conectado a: {active_ssid}" if active_ssid else "Conectado a: Nenhum"
        
        except Exception:
            current_status_str = "Conectado a: Nenhum" # Em caso de erro, assume desconectado
        
        # AQUI ESTÁ A LÓGICA DE NOTIFICAÇÃO
        if current_status_str != self.last_known_status_str:
            print(f"[WIFI Notify] Status mudou: '{self.last_known_status_str}' -> '{current_status_str}'. Enviando notificação.")
            self.last_known_status_str = current_status_str
            # Chama o método send_update da classe base para enviar a notificação
            self.send_update(current_status_str)

    @dbus.service.method(GATT_CHRC_IFACE,
                         in_signature='a{sv}',
                         out_signature='ay')
    def ReadValue(self, options):
        return [dbus.Byte(b) for b in self.last_known_status_str.encode('utf-8')]

class WifiCommandCharacteristic(Characteristic):
    WIFI_COMMAND_UUID = '12345678-1234-5678-1234-56789abcdef6' # NOVO UUID!
    
    def __init__(self, bus, index, service, connection_event):
        Characteristic.__init__(
            self, bus, index,
            self.WIFI_COMMAND_UUID,
            ['write'], # Flag correta
            service)
        self.connection_event = connection_event

    def _connect_wifi_task(self, ssid, password):
        """Esta função será executada em uma thread separada."""
        print(f"WifiConfig [Thread]: Iniciando conexão para SSID: {ssid}")
        try:
            # Tenta remover uma conexão existente
            subprocess.run(["sudo", "nmcli", "connection", "delete", ssid], check=False, capture_output=True)
            
            # Adiciona a nova conexão
            cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid, "password", password, "ifname", "wlan0", "name", ssid]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            print(f"WifiConfig [Thread]: nmcli connect output: {result.stdout}")
            self.current_ssid = ssid # Cuidado com race conditions se for crítico ler isso imediatamente
            print(f"WifiConfig [Thread]: Conexão Wi-Fi para '{ssid}' configurada com sucesso.")

        except subprocess.CalledProcessError as e:
            print(f"WifiConfig [Thread]: Erro ao configurar Wi-Fi com nmcli: {e}")
            print(f"nmcli stderr: {e.stderr}")
        except Exception as e:
            print(f"WifiConfig [Thread]: Erro inesperado na tarefa de conexão: {e}")
        finally:
            self.connection_event.set()

    def _disconnect_wifi_task(self):
        """Task para desconectar de todas as redes Wi-Fi gerenciadas por nmcli."""
        print("[WifiConfig] Iniciando tarefa de desconexão...")
        try:
            # Lista todas as conexões ativas
            list_cmd = ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"]
            result = subprocess.run(list_cmd, capture_output=True, text=True, check=True)

            # Procura por conexões na interface wlan0
            for line in result.stdout.strip().split('\n'):
                if 'wlan0' in line:
                    connection_name = line.split(':')[0]
                    print(f"[WifiConfig] Desativando conexão: {connection_name}")
                    # Desativa a conexão
                    disconnect_cmd = ["sudo", "nmcli", "connection", "down", connection_name]
                    subprocess.run(disconnect_cmd, check=True)
            
            print("[WifiConfig] Todas as conexões Wi-Fi ativas foram desativadas.")

        except Exception as e:
            print(f"[WifiConfig Thread] Erro ao desconectar: {e}")
        finally:
            # Força uma nova verificação de status para notificar o app
            self.connection_event.set()
            
    @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}', out_signature='')
    
    def WriteValue(self, value, options):
        try:
            json_str = bytes(value).decode('utf-8')
            print(f"WifiConfig: Received JSON string: {json_str}")
            data = json.loads(json_str)

            # Verifica se é um comando para ficar offline
            if data.get('command') == 'offline':
                thread = threading.Thread(target=self._disconnect_wifi_task)
                thread.daemon = True
                thread.start()
                return
                
            ssid = data.get('ssid')
            password = data.get('password')

            if ssid and password:
                print("WifiConfig: Dados válidos. Disparando tarefa de conexão em segundo plano.")
                
                # Inicia a função de conexão em uma nova thread
                thread = threading.Thread(target=self._connect_wifi_task, args=(ssid, password))
                thread.daemon = True  # Permite que o programa principal saia mesmo se a thread estiver rodando
                thread.start()

                # Retorna imediatamente para o BlueZ enviar o ACK de sucesso
                return
            else:
                raise exceptions.InvalidArgsException("SSID ou senha ausentes")

        except Exception as e:
            print(f"WifiConfig: Erro geral ao processar WriteValue: {e}")
            raise exceptions.FailedException("Erro ao processar o pedido.")


def register_app_cb():
    print('GATT application registered')


def register_app_error_cb(mainloop, error):
    print('Failed to register application: ' + str(error))
    mainloop.quit()

def is_internet_available():
    """
    Verifica o status REAL da conexão Wi-Fi. Retorna True se conectado, False caso contrário.
    Esta função encapsula a lógica do ReadValue para ser reutilizável.
    """
    try:
        # Comando para verificar se há uma conexão ativa com o NetworkManager
        cmd = ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Procura por uma linha que comece com "yes:", indicando uma conexão ativa.
        for line in result.stdout.strip().split('\n'):
            if line.startswith('yes:'):
                return True # Conexão ativa encontrada
        
        # Se o loop terminar, nenhuma conexão ativa foi encontrada
        return False
        
    except (subprocess.CalledProcessError, FileNotFoundError):
        # O comando falhou ou nmcli não foi encontrado
        return False
    except Exception as e:
        print(f"[Internet Check] Erro inesperado: {e}")
        return False

def gatt_server_main(mainloop, bus, adapter_name, connection_event):
    adapter = adapters.find_adapter(bus, GATT_MANAGER_IFACE, adapter_name)
    if not adapter:
        raise Exception('GattManager1 interface not found')
    service_manager = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter),
        GATT_MANAGER_IFACE)

    app = Application(bus, connection_event)
    
    print('Registering GATT application...')
    service_manager.RegisterApplication(app.get_path(), {},
                                        reply_handler=register_app_cb,
                                        error_handler=functools.partial(register_app_error_cb, mainloop))
    return app
