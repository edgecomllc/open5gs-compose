#!/usr/bin/env python3
"""
IMS SIP Registrar на основе Twisted с поддержкой TCP и UDP
Обрабатывает REGISTER, OPTIONS и SUBSCRIBE запросы
IMS Domain: ims.mnc099.mcc999.3gppnetwork.org
"""

from twisted.internet import reactor, protocol
from twisted.protocols import basic
from twisted.python import log

import sys
import hashlib
import time
import random
import re
import socket

# Настройка логирования
log.startLogging(sys.stdout)

class IMSRegistrarProtocol(basic.LineReceiver):
    """
    IMS SIP Registrar Protocol с поддержкой TCP.
    """
    
    def __init__(self, domain, credentials, local_ip):
        self.domain = domain
        self.registry = {}
        self.user_credentials = credentials
        self.buffer = b""
        self.local_ip = local_ip
        
    def connectionMade(self):
        """Вызывается при установке TCP соединения."""
        if self.transport:
            try:
                peer = self.transport.getPeer()
                log.msg(f"TCP connection established from {peer.host}:{peer.port}")
            except:
                log.msg("TCP connection established")
        self.setRawMode()
        
    def connectionLost(self, reason):
        """Вызывается при потере TCP соединения."""
        log.msg(f"TCP connection lost: {reason.getErrorMessage()}")
        
    def rawDataReceived(self, data):
        """
        Обработка сырых данных в RAW режиме.
        """
        self.buffer += data
        
        while self.buffer:
            msg_end = self.buffer.find(b"\r\n\r\n")
            
            if msg_end == -1:
                return
                
            msg_end += 4
            message_data = self.buffer[:msg_end]
            self.buffer = self.buffer[msg_end:]
            
            self.handle_sip_message(message_data, None)
            
    def handle_sip_message(self, data, addr=None):
        """
        Обработка SIP сообщения.
        """
        try:
            message_str = data.decode('utf-8', errors='ignore')
            log.msg(f"Received SIP message:\n{message_str}")
            
            lines = message_str.split('\r\n')
            if not lines:
                return
                
            first_line = lines[0]
            parts = first_line.split(' ')
            if len(parts) < 3:
                log.msg(f"Invalid first line: {first_line}")
                return
                
            method = parts[0]
            uri = parts[1]
            
            log.msg(f"Method: {method}, URI: {uri}")
            
            headers = {}
            body = []
            in_body = False
            
            for line in lines[1:]:
                if line == '' and not in_body:
                    in_body = True
                    continue
                if in_body:
                    body.append(line)
                else:
                    if ': ' in line:
                        key, value = line.split(': ', 1)
                        headers[key.lower()] = value
                    elif ':' in line:
                        key, value = line.split(':', 1)
                        headers[key.lower()] = value.strip()
                        
            log.msg(f"Parsed headers: {list(headers.keys())}")
            
            if method == 'REGISTER':
                self.handle_REGISTER(method, uri, headers, body, addr)
            elif method == 'OPTIONS':
                self.handle_OPTIONS(method, uri, headers, addr)
            elif method == 'SUBSCRIBE':
                self.handle_SUBSCRIBE(method, uri, headers, body, addr)
            else:
                log.msg(f"Unsupported method: {method}")
                self.send_response(405, 'Method Not Allowed', headers, addr)
                
        except Exception as e:
            log.msg(f"Error handling SIP message: {e}")
            import traceback
            traceback.print_exc()
            self.send_response(500, 'Server Error', {}, addr)
            
    def handle_OPTIONS(self, method, uri, headers, addr=None):
        """Обработка OPTIONS запроса."""
        log.msg("Handling OPTIONS request")
        self.send_response(200, 'OK', headers, addr)
        
    def handle_SUBSCRIBE(self, method, uri, headers, body, addr=None):
        """
        Обработка SUBSCRIBE запроса.
        Всегда возвращает 200 OK как в примере.
        """
        log.msg("Handling SUBSCRIBE request")
        
        call_id = headers.get('call-id', 'unknown')
        from_header = headers.get('from', 'unknown')
        to_header = headers.get('to', 'unknown')
        cseq = headers.get('cseq', '0')
        via = headers.get('via', 'SIP/2.0/UDP')
        contact = headers.get('contact', '')
        expires = headers.get('expires', '600000')
        
        # Формируем ответ как в примере
        response = f"SIP/2.0 200 OK\r\n"
        response += f"Via: {via}\r\n"
        response += f"Record-Route: <sip:{self.local_ip}:5060;lr;Hpt=8f62_116;CxtId=3;TRC=163d-ffffffff>\r\n"
        response += f"Call-ID: {call_id}\r\n"
        response += f"From: {from_header}\r\n"
        
        # Добавляем тег для To заголовка
        if ';tag=' not in to_header:
            to_header += f';tag={self._generate_tag()}'
        response += f"To: {to_header}\r\n"
        
        response += f"CSeq: {cseq} SUBSCRIBE\r\n"
        
        # Contact заголовок как в примере
        if contact:
            response += f"Contact: {contact}\r\n"
        else:
            response += f"Contact: <sip:{self.local_ip}:5060>\r\n"
        
        response += f"Expires: {expires}\r\n"
        response += f"P-Asserted-Identity: <sip:scscf.{self.domain}>\r\n"
        response += f"Content-Length: 0\r\n"
        response += "\r\n"
        
        log.msg("=" * 60)
        log.msg("SENDING SUBSCRIBE RESPONSE:")
        log.msg(response)
        log.msg("=" * 60)
        
        self._send_response_data(response.encode('utf-8'), addr)
            
    def handle_REGISTER(self, method, uri, headers, body, addr=None):
        """
        Обработка REGISTER запроса.
        """
        try:
            from_header = headers.get('from', '')
            to_header = headers.get('to', '')
            call_id = headers.get('call-id', 'unknown')
            cseq = headers.get('cseq', '0')
            contact = headers.get('contact', '')
            expires = int(headers.get('expires', '3600'))
            auth_header = headers.get('authorization', '')
            
            log.msg(f"REGISTER from: {from_header}")
            log.msg(f"REGISTER to: {to_header}")
            log.msg(f"REGISTER contact: {contact}")
            log.msg(f"REGISTER expires: {expires}")
            
            from_uri = self._extract_uri(from_header)
            to_uri = self._extract_uri(to_header)
            
            username = self._extract_username(to_uri)
            
            if not username:
                log.msg("No username found")
                self.send_response(400, 'Bad Request', headers, addr)
                return
                
            log.msg(f"Username: {username}")
                
            if auth_header:
                log.msg("Authorization header present")
                if self._verify_digest(auth_header, method, uri, username):
                    if expires == 0:
                        self._unregister_user(to_uri, contact)
                    else:
                        self._register_user(to_uri, contact, expires, headers)
                    
                    contacts = self._get_contacts_for_aor(to_uri)
                    self.send_ims_response(200, 'OK', headers, contacts, username, addr)
                else:
                    log.msg("Digest verification failed")
                    self.send_response(401, 'Unauthorized', headers, addr, auth_required=True)
            else:
                log.msg("No authorization header, challenging...")
                self.send_response(401, 'Unauthorized', headers, addr, auth_required=True)
                
        except Exception as e:
            log.msg(f"REGISTER error: {e}")
            import traceback
            traceback.print_exc()
            self.send_response(500, 'Server Error', headers, addr)
            
    def _get_contacts_for_aor(self, aor):
        """Получение всех активных контактов для AOR."""
        contacts = []
        current_time = time.time()
        
        for uri, data in list(self.registry.items()):
            if self._same_aor(uri, aor):
                if data['expires'] > current_time:
                    contact_info = {
                        'contact': data['contact'],
                        'expires': int(data['expires'] - current_time),
                        'q': data.get('q', 1.0)
                    }
                    if 'instance' in data:
                        contact_info['instance'] = data['instance']
                    if 'icsi' in data:
                        contact_info['icsi'] = data['icsi']
                    contacts.append(contact_info)
                else:
                    del self.registry[uri]
                    
        return contacts
        
    def _same_aor(self, uri1, uri2):
        """Проверка, принадлежат ли URI одному AOR."""
        user1 = self._extract_username(uri1)
        user2 = self._extract_username(uri2)
        
        if not user1 or not user2:
            return False
            
        domain1 = uri1.split('@')[1] if '@' in uri1 else ''
        domain2 = uri2.split('@')[1] if '@' in uri2 else ''
        
        return user1 == user2 and domain1 == domain2
        
    def _extract_uri(self, header):
        """Извлечение URI из заголовка."""
        if not header:
            return ""
        if '<' in header and '>' in header:
            return header[header.index('<')+1:header.index('>')]
        elif 'sip:' in header:
            match = re.search(r'sip:[^@]+@[^;>]+', header)
            if match:
                return match.group(0)
        return header
        
    def _extract_username(self, uri):
        """Извлечение имени пользователя из URI."""
        if 'sip:' in uri:
            match = re.search(r'sip:([^@]+)@', uri)
            if match:
                return match.group(1)
        return None
        
    def _extract_ims_params(self, contact_header):
        """Извлечение IMS параметров из Contact заголовка."""
        params = {}
        
        instance_match = re.search(r'\+sip\.instance\s*=\s*"([^"]+)"', contact_header)
        if instance_match:
            params['instance'] = instance_match.group(1)
            
        icsi_match = re.search(r'\+g\.3gpp\.icsi-ref\s*=\s*"([^"]+)"', contact_header)
        if icsi_match:
            params['icsi'] = icsi_match.group(1)
            
        if '+g.3gpp.smsip' in contact_header:
            params['smsip'] = True
            
        return params
        
    def _verify_digest(self, auth_header, method, uri, username):
        """Проверка Digest-аутентификации (всегда возвращает True для тестирования)."""
        try:
            if auth_header.lower().startswith('digest '):
                auth_header = auth_header[7:]
            
            params = {}
            current_key = None
            current_value = []
            in_quotes = False
            
            i = 0
            while i < len(auth_header):
                char = auth_header[i]
                
                if char == '"':
                    in_quotes = not in_quotes
                    current_value.append(char)
                elif char == ',' and not in_quotes:
                    if current_key:
                        value = ''.join(current_value).strip().strip('"')
                        params[current_key.strip()] = value
                    current_key = None
                    current_value = []
                elif char == '=' and not in_quotes and current_key is None:
                    current_key = ''.join(current_value).strip()
                    current_value = []
                else:
                    current_value.append(char)
                i += 1
            
            if current_key:
                value = ''.join(current_value).strip().strip('"')
                params[current_key.strip()] = value
            
            if not params:
                pattern = r'(\w+)\s*=\s*"([^"]*)"'
                params = dict(re.findall(pattern, auth_header))
            
            auth_username = params.get('username', '')
            realm = params.get('realm', '')
            
            log.msg(f"Parsed auth: username='{auth_username}', realm='{realm}'")
            
            # ВСЕГДА ВОЗВРАЩАЕМ True ДЛЯ ТЕСТИРОВАНИЯ
            log.msg("⚠️ AUTHENTICATION BYPASSED - always returning True")
            return True
            
        except Exception as e:
            log.msg(f"Digest verification error: {e}")
            import traceback
            traceback.print_exc()
            return True
            
    def _register_user(self, uri, contact, expires, headers):
        """Регистрация пользователя с IMS параметрами."""
        expiry_time = time.time() + expires
        
        contact_clean = contact.strip()
        if contact_clean.startswith('<') and contact_clean.endswith('>'):
            contact_clean = contact_clean[1:-1]
        
        q = 1.0
        contact_without_q = contact_clean
        if ';q=' in contact_clean:
            try:
                parts = contact_clean.split(';q=')
                contact_without_q = parts[0]
                q_str = parts[1].split(';')[0]
                q = float(q_str)
            except:
                pass
        
        ims_params = self._extract_ims_params(contact_clean)
        
        reg_data = {
            'contact': contact_without_q,
            'expires': expiry_time,
            'q': q
        }
        
        if 'instance' in ims_params:
            reg_data['instance'] = ims_params['instance']
        if 'icsi' in ims_params:
            reg_data['icsi'] = ims_params['icsi']
        if 'smsip' in ims_params:
            reg_data['smsip'] = True
            
        self.registry[uri] = reg_data
        
        log.msg(f"✅ Registered: {uri} -> {contact_without_q} (expires: {expires}s, q: {q})")
        if 'instance' in ims_params:
            log.msg(f"   Instance: {ims_params['instance']}")
        if 'icsi' in ims_params:
            log.msg(f"   ICSI: {ims_params['icsi']}")
        
    def _unregister_user(self, uri, contact):
        """Отмена регистрации пользователя."""
        contact_clean = contact.strip()
        if contact_clean.startswith('<') and contact_clean.endswith('>'):
            contact_clean = contact_clean[1:-1]
        if ';q=' in contact_clean:
            contact_clean = contact_clean.split(';q=')[0]
            
        to_delete = []
        for reg_uri, data in self.registry.items():
            stored_contact = data['contact']
            if reg_uri == uri and stored_contact == contact_clean:
                to_delete.append(reg_uri)
            elif self._same_aor(reg_uri, uri) and stored_contact == contact_clean:
                to_delete.append(reg_uri)
                
        for reg_uri in to_delete:
            del self.registry[reg_uri]
            log.msg(f"Unregistered: {reg_uri}")
            
    def send_ims_response(self, code, reason, headers, contacts, username, addr=None):
        """
        Отправка IMS-специфичного ответа 200 OK.
        """
        call_id = headers.get('call-id', 'unknown')
        from_header = headers.get('from', 'unknown')
        to_header = headers.get('to', 'unknown')
        cseq = headers.get('cseq', '0')
        via = headers.get('via', 'SIP/2.0/TCP')
        
        response = f"SIP/2.0 {code} {reason}\r\n"
        response += f"Via: {via}\r\n"
        response += f"Call-ID: {call_id}\r\n"
        response += f"From: {from_header}\r\n"
        
        if ';tag=' not in to_header:
            to_header += f';tag={self._generate_tag()}'
        response += f"To: {to_header}\r\n"
        
        response += f"CSeq: {cseq}\r\n"
        response += f"P-Associated-URI: <sip:+{username}@{self.domain}>,<sip:+{username}@{self.domain};user=phone>\r\n"
        
        for contact_info in contacts:
            contact = contact_info['contact']
            expires = contact_info['expires']
            q = contact_info.get('q', 1.0)
            
            contact_header = f"<{contact}>;q={q};expires={expires}"
            
            if 'instance' in contact_info:
                contact_header += f';+sip.instance="<{contact_info["instance"]}>"'
            if 'icsi' in contact_info:
                contact_header += f';+g.3gpp.icsi-ref="{contact_info["icsi"]}"'
            if 'smsip' in contact_info and contact_info['smsip']:
                contact_header += ';+g.3gpp.smsip'
                
            response += f"Contact: {contact_header}\r\n"
        
        if not contacts:
            response += f"Contact: *\r\n"
            
        response += f"Path: <sip:{self.local_ip}:5060;lr>\r\n"
        response += f"Content-Length: 0\r\n"
        response += "\r\n"
        
        log.msg("=" * 60)
        log.msg("SENDING IMS RESPONSE:")
        log.msg(f"Contacts count: {len(contacts)}")
        log.msg(response)
        log.msg("=" * 60)
        
        self._send_response_data(response.encode('utf-8'), addr)
            
    def send_response(self, code, reason, headers, addr=None, auth_required=False):
        """Отправка простого ответа без контактов."""
        call_id = headers.get('call-id', 'unknown')
        from_header = headers.get('from', 'unknown')
        to_header = headers.get('to', 'unknown')
        cseq = headers.get('cseq', '0')
        via = headers.get('via', 'SIP/2.0/TCP')
        
        response = f"SIP/2.0 {code} {reason}\r\n"
        response += f"Via: {via}\r\n"
        response += f"Call-ID: {call_id}\r\n"
        response += f"From: {from_header}\r\n"
        
        if ';tag=' not in to_header:
            to_header += f';tag={self._generate_tag()}'
        response += f"To: {to_header}\r\n"
        
        response += f"CSeq: {cseq}\r\n"
        response += f"Server: Simple-IMS-Registrar/1.0\r\n"
        
        if auth_required:
            nonce = self._generate_nonce()
            response += f'WWW-Authenticate: Digest realm="{self.domain}", nonce="{nonce}", algorithm=MD5\r\n'
            
        response += f"Content-Length: 0\r\n"
        response += "\r\n"
        
        log.msg("=" * 60)
        log.msg("SENDING RESPONSE:")
        log.msg(response)
        log.msg("=" * 60)
        
        self._send_response_data(response.encode('utf-8'), addr)
        
    def _send_response_data(self, data, addr=None):
        """
        Отправка данных через TCP или UDP.
        """
        if addr:
            # UDP - используем транспорт для отправки
            self.transport.write(data, addr)
        else:
            # TCP - обычная отправка
            self.transport.write(data)
        
    def _generate_tag(self):
        """Генерация уникального тега."""
        return f"tag{int(time.time() * 1000)}"
        
    def _generate_nonce(self):
        """Генерация nonce для аутентификации."""
        return hashlib.md5(f"{time.time()}:{random.random()}:{self.domain}".encode()).hexdigest()


class IMSRegistrarUDPProtocol(protocol.DatagramProtocol):
    """
    IMS SIP Registrar Protocol для UDP.
    """
    
    def __init__(self, domain, credentials, local_ip):
        self.domain = domain
        self.credentials = credentials
        self.local_ip = local_ip
        self.registry = {}
        
    def datagramReceived(self, data, addr):
        """
        Обработка UDP дейтаграммы.
        """
        log.msg(f"UDP datagram received from {addr}")
        
        try:
            tcp_handler = IMSRegistrarProtocol(self.domain, self.credentials, self.local_ip)
            tcp_handler.registry = self.registry
            tcp_handler.handle_sip_message(data, addr)
            
        except Exception as e:
            log.msg(f"Error handling UDP message: {e}")
            import traceback
            traceback.print_exc()


class IMSRegistrarFactory(protocol.Factory):
    """
    Фабрика для создания экземпляров IMSRegistrarProtocol (TCP).
    """
    
    def __init__(self, domain, credentials, local_ip):
        self.domain = domain
        self.credentials = credentials
        self.local_ip = local_ip
        
    def buildProtocol(self, addr):
        protocol = IMSRegistrarProtocol(self.domain, self.credentials, self.local_ip)
        protocol.factory = self
        return protocol
    
    def startFactory(self):
        log.msg(f"IMS Registrar TCP Factory started for domain {self.domain}")
        log.msg(f"Local IP: {self.local_ip}")
    
    def stopFactory(self):
        log.msg("IMS Registrar TCP Factory stopped")


if __name__ == "__main__":
    DOMAIN = "ims.mnc099.mcc999.3gppnetwork.org"
    PORT = 5060
    LOCAL_IP = "192.168.10.100"
    
    USER_CREDENTIALS = {
        f"sip:250020000000016@{DOMAIN}": "password",
        f"250020000000016@{DOMAIN}": "password",
        f"student@{DOMAIN}": "password",
        f"teacher@{DOMAIN}": "password",
        f"admin@{DOMAIN}": "admin123"
    }
    
    tcp_factory = IMSRegistrarFactory(DOMAIN, USER_CREDENTIALS, LOCAL_IP)
    udp_protocol = IMSRegistrarUDPProtocol(DOMAIN, USER_CREDENTIALS, LOCAL_IP)
    
    reactor.listenTCP(PORT, tcp_factory)
    log.msg(f"TCP listener started on port {PORT}")
    
    reactor.listenUDP(PORT, udp_protocol)
    log.msg(f"UDP listener started on port {PORT}")
    
    log.msg(f"Starting IMS Registrar on TCP and UDP port {PORT}")
    log.msg(f"IMS Domain: {DOMAIN}")
    log.msg(f"Local IP for Path header: {LOCAL_IP}")
    log.msg("Press Ctrl+C to stop...")
    
    reactor.run()
