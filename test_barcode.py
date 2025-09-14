#!/data/data/com.termux/files/usr/bin/python
"""
Script de debugging para códigos de barras en impresoras ESC/POS
Prueba diferentes formatos y parámetros para encontrar el que funciona
"""

import json
import sys
import time
from escpos.printer import Network
from datetime import datetime

def test_barcode_formats(ip, port, test_code="4K5TKMZT"):
    """
    Prueba diferentes formatos de códigos de barras
    """
    print(f"🧪 INICIANDO PRUEBAS DE CÓDIGO DE BARRAS")
    print(f"🖨️  Impresora: {ip}:{port}")
    print(f"🔤 Código de prueba: {test_code}")
    print("=" * 60)
    
    try:
        # Conectar a impresora
        printer = Network(ip, port=port, timeout=10)
        print("✅ Conexión establecida")
        
        # Encabezado
        printer.set(align='center', bold=True, width=2, height=2)
        printer.text("PRUEBA CÓDIGOS DE BARRAS\n")
        printer.set(align='center', bold=False, width=1, height=1)
        printer.text(f"Código: {test_code}\n")
        printer.text(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
        printer.text("=" * 48 + "\n")
        
        # Lista de formatos a probar
        barcode_tests = [
            # (nombre, formato, parámetros)
            ("CODE128 Básico", "CODE128", {}),
            ("CODE128 + Width", "CODE128", {"width": 2}),
            ("CODE128 + Width/Height", "CODE128", {"width": 2, "height": 60}),
            ("CODE128 Completo", "CODE128", {"width": 2, "height": 80, "pos": "BELOW"}),
            ("CODE39 Básico", "CODE39", {}),
            ("CODE39 + Width", "CODE39", {"width": 2}),
            ("CODE93 Básico", "CODE93", {}),
        ]
        
        # Si el código es numérico, probar formatos numéricos
        if test_code.isdigit():
            barcode_tests.extend([
                ("UPC-A (si 12 dígitos)", "UPC-A", {}),
                ("EAN13 (si 12+ dígitos)", "EAN13", {}),
                ("CODE128 Numérico", "CODE128", {"width": 2, "height": 60}),
            ])
        
        success_count = 0
        
        for i, (name, format_type, params) in enumerate(barcode_tests, 1):
            printer.text(f"\n{i}. Probando: {name}\n")
            printer.text("-" * 30 + "\n")
            
            try:
                # Preparar código según el formato
                test_value = test_code
                
                # Ajustes específicos por formato
                if format_type == "EAN13" and len(test_code) < 12:
                    test_value = test_code.ljust(12, "0")[:12]
                elif format_type == "UPC-A" and len(test_code) < 11:
                    test_value = test_code.ljust(11, "0")[:11]
                
                # Imprimir código de barras
                if params:
                    printer.barcode(test_value, format_type, **params)
                else:
                    printer.barcode(test_value, format_type)
                
                printer.text(f"✅ {name}: OK\n")
                printer.text(f"Código usado: {test_value}\n")
                success_count += 1
                print(f"✅ {name}: ÉXITO")
                
            except Exception as e:
                printer.text(f"❌ {name}: ERROR\n")
                printer.text(f"Error: {str(e)[:30]}...\n")
                print(f"❌ {name}: FALLÓ - {e}")
            
            printer.text("-" * 30 + "\n")
            time.sleep(0.5)  # Pausa entre pruebas
        
        # Resultados finales
        printer.text("\n" + "=" * 48 + "\n")
        printer.text("RESULTADOS FINALES:\n")
        printer.text(f"Formatos exitosos: {success_count}/{len(barcode_tests)}\n")
        printer.text(f"Fecha: {datetime.now().strftime('%H:%M:%S')}\n")
        
        # Prueba final con fallback visual
        printer.text("\nFALLBACK VISUAL:\n")
        printer.text("*" * 48 + "\n")
        printer.set(width=2, height=2, bold=True)
        printer.text(f"  {test_code}  \n")
        printer.set(width=1, height=1, bold=False)
        printer.text("*" * 48 + "\n")
        
        # Cortar papel
        try:
            printer.cut()
        except:
            printer.text("\n\n\n\n")
        
        printer.close()
        
        print("=" * 60)
        print(f"🏁 PRUEBAS COMPLETADAS: {success_count}/{len(barcode_tests)} exitosos")
        return success_count > 0
        
    except Exception as e:
        print(f"💥 ERROR GENERAL: {e}")
        return False

def test_playground_receipt_full(ip, port):
    """
    Prueba completa de recibo de parque como el sistema real
    """
    print(f"🎮 PRUEBA COMPLETA RECIBO PARQUE")
    
    # Datos de prueba (como los que tienes en la cola)
    test_data = {
        "order_id": 18201,
        "order_name": "Parque/0003", 
        "table": "",
        "customer": "Cliente General",
        "server": "Administrador",
        "datetime": "2025-09-14 15:15:01",
        "job_type": "playground_receipt",
        "company_name": "Churro Park",
        "amount_total": 8.0,
        "is_playground_receipt": True,
        "playground_codes": [{
            "product_name": "Entrada Parque 30 min",
            "qty": 1.0,
            "duration": 30,
            "code": "4K5TKMZT",
            "price_unit": 8.0,
            "price_subtotal": 8.0
        }],
        "regular_lines": [],
        "payments": [{"payment_method": "Efectivo", "amount": 8.0}]
    }
    
    try:
        printer = Network(ip, port=port, timeout=10)
        
        # === IMPRESIÓN IGUAL AL SISTEMA REAL ===
        company_name = test_data.get('company_name', 'PARQUE INFANTIL')
        tracking_number = test_data.get('order_name', 'N/A')
        
        # Encabezado
        printer.set(align='center', width=2, height=2, bold=True)
        printer.text(f"{company_name}\n")
        
        printer.set(align='center', width=1, height=1, bold=False)
        printer.text("=" * 48 + "\n")
        
        # Información básica
        printer.set(align='left', bold=True)
        printer.text(f"ENTRADA PARQUE: {tracking_number}\n")
        printer.set(bold=False)
        
        order_date = test_data.get('datetime', datetime.now().strftime('%d/%m/%Y %H:%M'))
        printer.text(f"Fecha: {order_date}\n")
        
        server = test_data.get('server', 'N/A')
        customer = test_data.get('customer', 'Cliente General')
        printer.text(f"Mesero: {server}\n")
        if customer != 'Cliente General':
            printer.text(f"Cliente: {customer[:40]}\n")
        
        printer.text("=" * 48 + "\n")
        
        # Códigos de parque
        playground_codes = test_data.get('playground_codes', [])
        
        if playground_codes:
            printer.set(align='center', bold=True, width=1, height=2)
            printer.text("🎮 ENTRADAS PARQUE INFANTIL 🎮\n")
            printer.set(align='left', bold=False, width=1, height=1)
            printer.text("=" * 48 + "\n")
            
            for i, code_data in enumerate(playground_codes, 1):
                product_name = code_data.get('product_name', 'Entrada Parque')
                qty = int(code_data.get('qty', 1))
                duration = code_data.get('duration', 0)
                code = code_data.get('code', '')
                price = float(code_data.get('price_subtotal', 0))
                
                # Info del producto
                printer.set(bold=True)
                printer.text(f"{i}. {product_name}\n")
                printer.set(bold=False)
                printer.text(f"Cantidad: {qty} | Duración: {duration} min\n")
                printer.text(f"Precio: Bs.{price:.2f}\n")
                printer.text("-" * 48 + "\n")
                
                # CÓDIGOS DE BARRAS CON MÚLTIPLES INTENTOS
                if code:
                    printer.set(align='center')
                    printer.set(bold=True)
                    printer.text("🎯 CODIGO DE ACCESO 🎯\n")
                    printer.set(bold=False)
                    
                    # Múltiples intentos
                    barcode_success = False
                    attempts = [
                        ("CODE128 básico", lambda: printer.barcode(code, 'CODE128')),
                        ("CODE128 con width", lambda: printer.barcode(code, 'CODE128', width=2)),
                        ("CODE128 completo", lambda: printer.barcode(code, 'CODE128', width=2, height=60)),
                        ("CODE39", lambda: printer.barcode(code, 'CODE39')),
                    ]
                    
                    for attempt_name, attempt_func in attempts:
                        if not barcode_success:
                            try:
                                attempt_func()
                                printer.text("\n")
                                barcode_success = True
                                print(f"✅ Código de barras exitoso: {attempt_name}")
                                break
                            except Exception as e:
                                print(f"⚠️ {attempt_name} falló: {e}")
                    
                    # Fallback visual si todo falla
                    if not barcode_success:
                        print(f"❌ TODOS los códigos de barras fallaron, usando fallback visual")
                        printer.text("*" * 48 + "\n")
                        printer.set(width=2, height=2, bold=True)
                        printer.text(f"  {code}  \n")
                        printer.set(width=1, height=1, bold=False)
                        printer.text("*" * 48 + "\n")
                        printer.text("** ESCANEAR CÓDIGO MANUAL **\n")
                        printer.text("*" * 48 + "\n")
                    
                    # Código como texto
                    printer.set(align='center', bold=True)
                    printer.text(f"Código: {code}\n")
                    printer.set(bold=False)
                    
                    printer.set(align='left')
                    printer.text("=" * 48 + "\n")
        
        # Total
        printer.text("=" * 48 + "\n")
        total_final = test_data.get('amount_total', 0)
        printer.set(bold=True, width=1, height=2)
        printer.text(f"{'TOTAL:':<24} Bs.{total_final:>11.2f}\n")
        printer.set(bold=False, width=1, height=1)
        printer.text("=" * 48 + "\n")
        
        # Pagos
        payments = test_data.get('payments', [])
        if payments:
            printer.text("Método(s) de pago:\n")
            for payment in payments:
                method = payment.get('payment_method', 'Efectivo')
                amount = float(payment.get('amount', 0))
                printer.text(f"  {method}: Bs.{amount:.2f}\n")
        
        # Pie
        printer.set(align='center')
        printer.set(bold=True)
        printer.text("🎈 ¡DISFRUTEN EL PARQUE! 🎈\n")
        printer.set(bold=False)
        printer.text(f"Impreso: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        
        # Cortar
        try:
            printer.cut()
        except:
            printer.text("\n\n\n\n")
        
        printer.close()
        print("✅ Prueba completa de recibo terminada")
        return True
        
    except Exception as e:
        print(f"💥 ERROR en prueba completa: {e}")
        return False

def main():
    if len(sys.argv) < 3:
        print("Uso: python debug_barcode.py <IP> <PUERTO> [código_opcional]")
        print("Ejemplo: python debug_barcode.py 192.168.1.122 9100")
        print("Ejemplo: python debug_barcode.py 192.168.1.122 9100 4K5TKMZT")
        sys.exit(1)
    
    ip = sys.argv[1]
    port = int(sys.argv[2])
    test_code = sys.argv[3] if len(sys.argv) > 3 else "4K5TKMZT"
    
    print("SELECCIONE TIPO DE PRUEBA:")
    print("1. Prueba rápida de formatos de código de barras")
    print("2. Prueba completa de recibo de parque") 
    print("3. Ambas pruebas")
    
    choice = input("Opción (1-3): ").strip()
    
    if choice in ["1", "3"]:
        print("\n" + "="*60)
        test_barcode_formats(ip, port, test_code)
    
    if choice in ["2", "3"]:
        print("\n" + "="*60)
        test_playground_receipt_full(ip, port)
    
    print("\n🏁 TODAS LAS PRUEBAS COMPLETADAS")

if __name__ == "__main__":
    main()