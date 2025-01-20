from concurrent import futures
import grpc
import mysql.connector  
import user_pb2
import user_pb2_grpc
from threading import Lock
import prometheus_client
import datetime
import os
import time

# Cache per la politica "At Most Once"
request_cache = {}
cache_lock = Lock()

# Metriche Prometheus
requests_total = prometheus_client.Counter('grpc_server_requests_total', 'Total number of requests received', ['service', 'method', 'node'])
errors_total = prometheus_client.Counter('grpc_server_errors_total', 'Total number of errors', ['service', 'method', 'node'])
response_time = prometheus_client.Gauge('grpc_server_response_time_seconds', 'Response time for gRPC methods', ['service', 'method', 'node'])
active_connections = prometheus_client.Gauge('grpc_server_active_connections', 'Number of active gRPC connections', ['service', 'node'])

SERVICE_NAME = "grpc_server"
NODE_NAME = "node1"


class UserService(user_pb2_grpc.UserServiceServicer):
    
    def __init__(self):
        # Connessione al database MySQL
        self.conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'host.docker.internal'),  
            user=os.getenv('MYSQL_USER', 'root'),  
            password=os.getenv('MYSQL_PASSWORD', 'root'), 
            database=os.getenv('MYSQL_DATABASE', 'db'),  
            port=int(os.getenv('MYSQL_PORT', 3306))
        )
        self.cursor = self.conn.cursor()
        # Crea la tabella se non esiste
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            high_value DOUBLE,
            low_value DOUBLE
        )
        """)
        self.conn.commit()


    def RegisterUser(self, request, context):
        method_name = "RegisterUser"
        start_time = time.time()



        # Verifica se l'email è già presente nella cache
        with cache_lock:
            if email in request_cache:
                print(f"Email {email} già processata, restituendo la risposta dalla cache.")
                return request_cache[email]
        try:
            # Incrementa il contatore delle richieste
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            email = request.email
            ticker = request.ticker
            high_value = request.high_value
            low_value = request.low_value

            # Verifica che entrambi i valori siano forniti
            if high_value is None or low_value is None:
                return user_pb2.UserResponse(
                    success=False,
                    message="high_value e low_value devono essere forniti entrambi"
                )

            #Verifica che high_value è maggiore di low_value
            if high_value <= low_value:
                return user_pb2.UserResponse(
                    success=False,
                    message="high_value deve essere maggiore di low_value"
                )


            # Verifica se l'utente esiste già nel database
            self.cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            if self.cursor.fetchone():
                return user_pb2.UserResponse(
                    success=False,
                    message="Utente già registrato"  
                )
            else:
                # Registra il nuovo utente
                self.cursor.execute("INSERT INTO users (email, ticker, high_value, low_value) VALUES (%s, %s, %s, %s)", (email, ticker, high_value, low_value))
                self.conn.commit()  # Salva i cambiamenti nel database
                response = user_pb2.UserResponse(
                    success=True,
                    message="Utente registrato con successo"
                )

            # Memorizza la risposta nella cache
            with cache_lock:
                request_cache[email] = response

            return response
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            return user_pb2.UserResponse(
                success=False,
                message=f"Errore durante la registrazione: {str(e)}"
            )
        finally:
            # Registra il tempo di risposta
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)
        

    def UpdateUserTicker(self, request, context):
        method_name = "UpdateUserTicker"
        start_time = time.time()
        email = request.email
        new_ticker = request.ticker

        # Verifica se l'email è già presente nella cache
        with cache_lock:
            if email in request_cache:
                print(f"Email {email} già processata, restituendo la risposta dalla cache.")
                return request_cache[email]

        try:
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            # Verifica se l'utente esiste già nel database
            self.cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = self.cursor.fetchone()
            
            if not user:
                response = user_pb2.UserResponse(
                    success=False,
                    message="Utente non trovato"
                )
            else:
                # Verifica se il ticker da aggiornare è uguale al ticker attuale
                current_ticker = user[1]  # Il ticker è il secondo campo nella tabella
                if current_ticker == new_ticker:
                    response = user_pb2.UserResponse(
                        success=False,
                        message="Il codice dell'azione è già aggiornato"
                    )
                else:
                    # Esegue l'aggiornamento del ticker nel database
                    self.cursor.execute("UPDATE users SET ticker = %s WHERE email = %s", (new_ticker, email))
                    self.conn.commit()  # Salva le modifiche nel database
                    response = user_pb2.UserResponse(
                        success=True,
                        message="Ticker aggiornato con successo"
                    )

            # Memorizza la risposta nella cache
            with cache_lock:
                request_cache[email] = response
            return response
        
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            return user_pb2.UserResponse(
                success=False,
                message=f"Errore durante l'aggiornamento: {str(e)}"
            )
        finally:
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)
    
    def DeleteUser(self, request, context):
        email = request.email
        method_name = "DeleteUser"
        start_time = time.time()

        try:
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            # Verifica se l'utente esiste nel database
            self.cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = self.cursor.fetchone()
            
            if not user:
                response = user_pb2.UserResponse(
                    success=False,
                    message="Utente non trovato"
                )
            else:
                # Cancella l'utente dalla tabella 'users'
                self.cursor.execute("DELETE FROM users WHERE email = %s", (email,))
                self.conn.commit()
                
                response = user_pb2.UserResponse(
                    success=True,
                    message=f"Utente con email {email} cancellato con successo."
                )

            # Memorizza la risposta nella cache 
            with cache_lock:
                request_cache[email] = response
            return response
        
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            return user_pb2.UserResponse(
                success=False,
                message=f"Errore durante la cancellazione dell'utente: {str(e)}"
            )  
        finally:
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)

    # Funzione per ottenere il ticker associato a un utente
    def get_ticker_from_user(self, email):
        try:
            print(f"Recupero il ticker per l'email: {email}")
            self.cursor.execute("SELECT ticker FROM users WHERE email = %s", (email,))
            result = self.cursor.fetchone()  
            if result:
                return result[0]  # Restituisce il ticker
            else:
                return None  # Nessun ticker trovato per l'utente
        except Exception as e:
            print(f"Errore nel recupero del ticker: {str(e)}")
            return None

    # Funzione per ottenere l'ultimo valore disponibile per un ticker
    def get_latest_value(self, ticker):
        try:
            print(f"[DEBUG] Recupero i dati finanziari per il ticker: {ticker}")
            self.cursor.execute("""
                SELECT value, timestamp
                FROM financial_data
                WHERE ticker = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (ticker,))
            result = self.cursor.fetchone()  # Ottiene l'ultimo record
            print(f"[DEBUG] Risultato della query per ticker {ticker}: {result}")
            if result:
                value, timestamp = result
                print(f"[DEBUG] Tipo di dato del timestamp: {type(timestamp)}")  # Log del tipo di dato
                # Se il timestamp è un oggetto datetime viene convertito in stringa
                if isinstance(timestamp, datetime.datetime):
                    timestamp = timestamp.strftime('%Y-%m-%d %H:%M:%S')  

                return {"value": value, "timestamp": timestamp}
            else:
                return None  # Nessun dato disponibile per il ticker
        except Exception as e:
            print(f"Errore nel recupero dei dati finanziari: {str(e)}")
            return None

    def GetLatestValue(self, request, context):
        method_name = "GetLatestValue"
        start_time = time.time()
        """      
        Returns:
        - UserResponse: Contiene il valore del ticker e il timestamp.
        """

        try:
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            email = request.email
            print(f"[DEBUG] Richiesta ricevuta per email: {email}")

            # Ottiene il ticker dell'utente
            ticker = self.get_ticker_from_user(email)
            print(f"[DEBUG] Ticker trovato per email {email}: {ticker}")
            
            if not ticker:
                return user_pb2.UserResponse(
                    success=False,
                    message="Utente non trovato"
                )
            
            # Ottiene l'ultimo valore del ticker
            data = self.get_latest_value(ticker)
            print(f"[DEBUG] Dati finanziari trovati per ticker {ticker}: {data}")
            
            if not data:
                return user_pb2.UserResponse(
                    success=False,
                    message=f"Nessun dato disponibile per {ticker}"
                )
            

            timestamp = str(data['timestamp'])

            print(f"[DEBUG] Risposta inviata al client: Valore = {data['value']}, Timestamp = {timestamp}")



           
            return user_pb2.UserResponse(
                success=True,
                message=f"Ultimo valore per {ticker}: {data['value']} (Timestamp: {timestamp})",
                value=data['value'],
                timestamp=timestamp 
            )
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            print(f"Errore nel metodo GetLatestValue: {str(e)}")
            context.set_code(grpc.StatusCode.UNKNOWN)
            context.set_details(f"Errore nel server: {str(e)}")
            raise
        finally:
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)


    def CalculateAverage(self, request, context):
        method_name = "CalculateAverage"
        start_time = time.time()
        """
        Calcola la media degli ultimi X valori di un titolo associato all'utente.
        """
        email = request.email
        count = request.count

        try:
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            # Ottiene il ticker associato all'utente
            ticker = self.get_ticker_from_user(email)
            if not ticker:
                return user_pb2.AverageResponse(
                    success=False,
                    message="Utente non trovato o ticker non associato"
                )
            
            # Recupera gli ultimi X valori del ticker dalla tabella financial_data
            self.cursor.execute("""
                SELECT value 
                FROM financial_data
                WHERE ticker = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (ticker, count))
            
            results = self.cursor.fetchall()
            if not results:
                return user_pb2.AverageResponse(
                    success=False,
                    message=f"Nessun dato disponibile per {ticker}"
                )
            
            # Calcolo della media degli ultimi X valori
            values = [row[0] for row in results]
            average = sum(values) / len(values)


            return user_pb2.AverageResponse(
                success=True,
                message=f"Media calcolata con successo per gli ultimi {len(values)} valori",
                average=average
            )
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            context.set_code(grpc.StatusCode.UNKNOWN)
            context.set_details(f"Errore durante il calcolo della media: {str(e)}")
            raise
        finally:
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)
        

    def UpdateThreshold(self, request, context):
        method_name = "UpdateThreshold"
        start_time = time.time()
        email = request.email
        high_value = request.high_value if request.HasField('high_value') else None
        low_value = request.low_value if request.HasField('low_value') else None

        try:
            requests_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            # Verifica che entrambi i valori siano forniti
            if high_value is None or low_value is None:
                return user_pb2.UserResponse(
                    success=False,
                    message="high_value e low_value devono essere forniti entrambi"
                )

            # Validazione che high_value è maggiore di low_value
            if high_value <= low_value:
                return user_pb2.UserResponse(
                    success=False,
                    message="high_value deve essere maggiore di low_value"
                )

            self.cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            if not self.cursor.fetchone():
                return user_pb2.UserResponse(success=False, message="Utente non trovato")

            # Aggiorna i valori nel database
            self.cursor.execute("""
                UPDATE users
                SET high_value = %s, low_value = %s
                WHERE email = %s
            """, (high_value, low_value, email))
            self.conn.commit()

            return user_pb2.UserResponse(success=True, message="Valori aggiornati con successo")
        except Exception as e:
            errors_total.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).inc()
            return user_pb2.UserResponse(success=False, message=f"Errore: {str(e)}")  
        finally:
            response_time.labels(service=SERVICE_NAME, method=method_name, node=NODE_NAME).set(time.time() - start_time)  





# Funzione per avviare il server gRPC
def serve():
    # Avvia il server Prometheus per esporre le metriche
    prometheus_client.start_http_server(9100)
    active_connections.labels(service=SERVICE_NAME, node=NODE_NAME).inc()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    user_pb2_grpc.add_UserServiceServicer_to_server(UserService(), server)
    server.add_insecure_port('[::]:50051')  
    print("Server gRPC in esecuzione sulla porta 50051...")
    try:
        server.start()
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("Server interrotto manualmente.")
    finally:
        active_connections.labels(service=SERVICE_NAME, node=NODE_NAME).dec()


if __name__ == "__main__":
    serve()